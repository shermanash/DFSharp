
# coding: utf-8

# In[1]:

import pandas as pd
import matplotlib as plt
import seaborn as sns
import numpy as np
from patsy import dmatrices
import statsmodels.api as sm
import pickle
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import requests
from fuzzywuzzy import fuzz
from fuzzywuzzy import process
from collections import defaultdict
from collections import OrderedDict
from fuzzywuzzy import fuzz
from fuzzywuzzy import process
import cnfg
import tweepy
from requests_oauthlib import OAuth1
import re

pd.set_option("display.max_rows",300)

get_ipython().magic(u'matplotlib inline')


# In[2]:


'''
it loads latest model and todays_players CSV, dynamically determines
starters, generates today's projections, and pushes them up to elasticsearch

---
also generates today's optimal lineup and pushes THAT to elasticsearch
'''

config = cnfg.load("/Users/shermanash/ds/dfsharp_test/.twitter_develop")
oauth = OAuth1(config["consumer_key"],
               config["consumer_secret"],
               config["access_token"],
               config["access_token_secret"])

auth = tweepy.OAuthHandler(config["consumer_key"],
                           config["consumer_secret"])
auth.set_access_token(config["access_token"],
                      config["access_token_secret"])

api = tweepy.API(auth)

emoticons_str = r"""
    (?:
        [:=;] # Eyes
        [oO\-]? # Nose (optional)
        [D\)\]\(\]/\\OpP] # Mouth
    )"""

regex_str = [
    emoticons_str,
    r'<[^>]+>', # HTML tags
    r'(?:@[\w_]+)', # @-mentions
    r"(?:\#+[\w_]+[\w\'_\-]*[\w_]+)", # hash-tags
    r'http[s]?://(?:[a-z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-f][0-9a-f]))+', # URLs

    r'(?:(?:\d+,?)+(?:\.?\d+)?)', # numbers
    r"(?:[a-z][a-z'\-_]+[a-z])", # words with - and '
    r'(?:[\w_]+)', # other words
    r'(?:\S)' # anything else
]

tokens_re = re.compile(r'('+'|'.join(regex_str)+')', re.VERBOSE | re.IGNORECASE)
emoticon_re = re.compile(r'^'+emoticons_str+'$', re.VERBOSE | re.IGNORECASE)

days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
day_nums = [x for x in range(7)]
day_dict = dict(zip(days, day_nums))
dict_day = dict(zip(day_nums, days))
today = datetime.today()
daystr = dict_day[today.weekday()]


# In[3]:

def soup_url(url):
    site = requests.get(url)
    page = site.text
    soup = BeautifulSoup(page)
    return soup

def make_depth_dict():
    positions = ['PG', 'SG', 'SF', 'PF', 'C']
    url = 'http://basketball.realgm.com/nba/depth-charts'
    soup = soup_url(url)
    keys = []
    options = soup.find_all(class_='ddl')
    teams = options[1].find_all('option')[1:]
    i = 0
    for team in teams:
        #temp = process.extractOne(team.text, team_dict.keys())
        temp = process.extractOne(team.text, team_names)
        #key = team_dict[temp[0]]
        #keys.append(key)
        keys.append(temp[0])
    data = soup.find_all('table', class_="basketball")
    depth_dict = {}
    for datum in data:
        starters = datum.find_all(class_='depth_starters')
        starter_links = starters[0].find_all('a')
        starting5 = []
        roster = defaultdict(str)
        for starter in starter_links:
            starting5.append([starter.text])
        depth = dict(zip(positions, starting5))
        starting5 = np.ravel(starting5)
        starting_lineup = dict(zip(starting5, positions))
        subs = datum.find_all(class_='depth_rotation')
        rotation = defaultdict(str)
        for sub in subs:
            for pos in positions:
                links = sub.find_all('td', {'data-th': pos})
                for link in links:
                    if link.find('a'):
                        depth[pos].append(link.find('a').text)
                        rotation[link.find('a').text] = pos
                        
        scrubs = datum.find_all(class_="depth_limpt")
        scrub_dict = defaultdict(str)
        for scrub in scrubs:
            for pos in positions:
                    links = scrub.find_all('td', {'data-th': pos})
                    for link in links:
                        if link.find('a'):
                            depth[pos].append(link.find('a').text)
                            scrub_dict[link.find('a').text] = pos
        for player, position in starting_lineup.iteritems():
            roster[player] = position
        for player, position in rotation.iteritems():
            roster[player] = position
        for player, position in scrub_dict.iteritems():
            roster[player] = position
        depth_dict[keys[i]] = {'roster': roster,  'depth': depth, 'starters': starting_lineup, 'rotation': rotation, 'scrubs': scrub_dict}
        i += 1
    #depth_dict['PHO'] = depth_dict['PHX']
    return depth_dict


# In[4]:

""" init starters:
        grabs current rosters from realGM
    inputs: dataframe of today's players
    outputs: df with 1 on all players who fuzzy match a starter in realGM
"""
def init_starters(df):
    depth = make_depth_dict()
    starters = []
    # append starters for each pos to empty starters list [in linear time?!]
    for i in depth:
        starters.append(depth[i]['starters'].keys()[0])
        starters.append(depth[i]['starters'].keys()[1])
        starters.append(depth[i]['starters'].keys()[2])
        starters.append(depth[i]['starters'].keys()[3])
        starters.append(depth[i]['starters'].keys()[4])    
        
    #starters  = list of starters obtained from RealGM
    # name - name in row
    def starter_match(name):
        top = process.extractOne(name, starters)
        if top[1] > 85:
            # print(top[0])
            return(True)
        else:
            return(False)
         
    df['Start_Raw'] = df['name'].isin(starters)
    df['Start'] = df['name'].apply(starter_match)
    return(df)


# In[5]:

# ###First Scrape Reliable Injury Update Website

def scrape_injury_report():
    injury_dict = {}
    soup = soup_url('http://www.donbest.com/nba/injuries/')
    rows1 = soup.find_all('td', class_="otherStatistics_table_alternateRow statistics_cellrightborder")
    rows2 = soup.find_all('td', class_="statistics_table_row statistics_cellrightborder")
    row_types = [rows1, rows2]
    for rows in row_types:
        for i in range(0, len(rows), 5):
            details = {}
            details['update_date'] = datetime.strptime(rows[i].text, '%m/%d/%y')
            details['position'] = rows[i+1].text
            details['injury'] = rows[i+3].text
            details['update'] = rows[i+4].text
            injury_dict[rows[i+2].text] = details

    return injury_dict

def next_weekday(d, weekday):
    days_ahead = weekday - d.weekday()
    if (days_ahead < 0): # Target day already happened this week
        days_ahead += 7
    return d + timedelta(days_ahead)

def parse_injury_report(injury_dictionary):
    for player, injury in injury_dictionary.iteritems():
        predictors = ['"?"', 'probable', 'doubtful', 'miss', 'out']
        words = injury['update'].split()
        for i in range(len(words)):
            for day in days:
                if fuzz.ratio(words[i], day) > 90:
                    injury['status_date'] = next_weekday(injury['update_date'], day_dict[day])
                    injury['status'] = words[i-1]
        for word in words:
            for predictor in predictors:
                if fuzz.ratio(word, predictor) > 95:
                    injury['status'] = word
    return injury_dictionary


# In[6]:

# add real status to status column
def adjust_status(row):
    for key, value in injury_updates.iteritems():
        if key == row['name']:
            return(value['status'])

# if we have no 7d info, use 90day info
def rollback_minutes(row):
    if pd.isnull(row['min_7d_avg']) == True:
        return(row['min_90d_avg']*.90)
    else:
        return(row['min_7d_avg'])
    
# use status column to adjust min_proj
def apply_status(row):
    goodlist = ['probable', 'start', 'starting','playing','PLAYING','STARTING','START']
    qlist = ['questionable', '"?"','QUESTIONABLE']
    badlist = ['doubtful','out','miss','DOUBTFUL','OUT']
    if row['status'] in badlist:
        return(0.0)
    elif row['status'] in qlist:
        return(rollback_minutes(row)*.90)
    elif row['status'] in goodlist:
        return(rollback_minutes(row))
    else:
        return(rollback_minutes(row))
    
def zero_out(row):
    badlist = ['doubtful','out','miss']
    if row['status'] in badlist:
        return(0.0)
    else:
        return(row['DK_Proj'])


# In[7]:

''' reads injury report
    -adjusts projections it finds based on
    starting, playing, probable - no change
    questionable - subtract 5 minutes
    doubtful, out - 0
    
    outputs dataframe with adjusted minutes column
'''
def read_injury_report(df):
    
    local_df = df
    #local_df['min_proj'] = local_df['min_7d_avg']
    # add empty status column
    local_df['status'] = None
    
    # adjust min_proj if min_7d is empty
    local_df['min_proj'] = local_df.apply(rollback_minutes, axis=1)
    
    # get injury info
    injury_dict = scrape_injury_report()
    injury_updates = parse_injury_report(injury_dict)
    
    # add info to status column ****** NEEDS FUZZY MATCHING!!****
    local_df['status'] = local_df.apply(adjust_status, axis=1)
    
    return(local_df)
    


# In[8]:

# structure tweets into dataframe
def structure_results(results):
    id_list=[tweet.id for tweet in results]
    data=pd.DataFrame(id_list,columns=['id'])
    
    data["text"]= [tweet.text.encode('utf-8') for tweet in results]
    data["datetime"]=[tweet.created_at for tweet in results]
    
    return data
# get twitter updates from baskmonster (THE best!)
def get_twitter_updates(n=50):
    updates = []
    #for tweet in tweepy.Cursor(api.user_timeline, id="FantasyLabsNBA").items(n):
    #    updates.append(tweet)
    #for tweet in tweepy.Cursor(api.user_timeline, id="Rotoworld_BK").items(n):
    #    updates.append(tweet)
    for tweet in tweepy.Cursor(api.user_timeline, id=46653066).items(n):
        #print(tweet["text"])
        updates.append(tweet)
    injuries=structure_results(updates)
    return injuries


# In[9]:

# extract player name from tweets
def get_name(row):
    splitlist = row['text'].split()
    three = splitlist[5]
    four = splitlist[6]
    if three == daystr:
        name = ' '.join(splitlist[:2])
    elif four == daystr:
        name = ' '.join(splitlist[:3])
    else:
        name = None
    return(name)
# extract status from tweets
def get_status(row):
    splitlist = row['text'].split()
    three = splitlist[5]
    four = splitlist[6]
    if three == daystr:
        status = splitlist[4]
    elif four == daystr:
        status = splitlist[5]
    else:
        status = None
    return(status)


# In[10]:

''' merge twitter info
    inputs- dataframe of today's players
    
    outputs- left merged dataframe with updated status and tweet column
'''
def merge_status(row):
    # if we have no get status, use old status
    if pd.isnull(row['get_status']) == True:
        return(row['status'])
    else:
        return(row['get_status'])

def merge_twitter_info(df):
    updates = get_twitter_updates()  
    # sort so newer entries will override older entries
    updates.sort_values(by='datetime', inplace=True)

    # extract name and status
    updates['name'] = updates.apply(get_name, axis=1)
    updates['get_status'] = updates.apply(get_status, axis=1)
    dropped = updates.dropna()
    grouped = dropped.groupby('name')
    single_status = pd.DataFrame()
    for x,y in grouped:
        single_status = single_status.append(y.tail(1))
    tweets = single_status[['text','name','get_status']]
        
    merged_depth = pd.merge(left=df,right=single_status, how='left', left_on='name', right_on='name')
    merged_depth['status'] = merged_depth.apply(merge_status, axis=1)
    
    return(merged_depth)
    


# In[11]:

''' project today - makes projections for today's games
    inputs:
        - trained model
        - df containing today's players
        
    outputs:
        - df with added projections and value cols
'''
def project_today(model, df):
    
    # adjust min_proj with status info
    df['min_proj'] = df.apply(apply_status, axis=1)
    
    today_df = df[['dk_pos','dk_sal','Team','name','status','Start','min_proj',
                   'min_7d_avg','dk_avg_90_days','dk_std_90_days','dk_max_30_days',
                   'home','dk_per_min','opppts_avg']].dropna(subset=['dk_sal','Start','min_proj','dk_per_min','home','opppts_avg'])
    # add intercept and convert all to numeric
    Y_fake, features_real = dmatrices('''dk_sal ~ Start  + min_proj + dk_per_min 
                                 + home + opppts_avg 
                 ''', data=today_df, return_type='dataframe')
    
    # MAKE LIVE PROJECTIONS <3
    today_df['DK_Proj'] = (model.predict(features_real, transform=False))
    today_df['DK_Proj'] = today_df['DK_Proj']**2
    
    today_df['proj_pure'] = today_df['min_proj'] * today_df['dk_per_min']
    
    today_df['value'] = today_df['DK_Proj'] / (today_df['dk_sal'] / 1000) 
    today_df['ceiling'] = today_df['DK_Proj'] + today_df['dk_std_90_days']
    
    today_df['DK_Proj'] = today_df.apply(zero_out, axis=1)
    return(today_df)


# In[12]:

# (load team name list)
team_walk = pd.read_csv('/Users/shermanash/ds/nba/dfs_twitter/team_crosswalk.csv', sep='\t')
team_names = team_walk.team_long.tolist()


# In[13]:

# load latest model
path = '/Users/shermanash/ds/dfsharp_test/latest_model.p'
model = pickle.load( open( path, "rb" ) )


# In[14]:

# get DF of todays players
filename = today.strftime('%Y%m%d')+'_players.csv'
todays_players = pd.read_csv(filename)


# In[15]:

# 6) generate today's starters
starters = init_starters(todays_players)

# 6.25) initialize injury report from donbest
inj_dict = scrape_injury_report()
injury_updates = parse_injury_report(inj_dict)
# 6.5) get latest injury news
injuries = read_injury_report(starters)

# 6.75) merge in latest twitter injury news [now status column is up, but minute aren't updated]
twitter = merge_twitter_info(injuries)

# 7) generate today's projections [ update minutes first!]
today_proj = project_today(model, twitter)
# 8) push timestamped projections to elasticsearch

optfile = today.strftime('%Y%m%d')+'_opt.csv'
opt_path = '/home/ubuntu/dfsharp/opt_csvs/'+optfile
today_proj['numpos'] = today_proj['dk_pos'].map({1 : 'PG', 2 : 'SG', 3 : 'SF', 4: 'PF', 5 : 'C'})
hio = today_proj[['numpos','name','dk_sal','Start','DK_Proj','value',
                  'ceiling','min_proj','dk_per_min','home','Team','opppts_avg']].to_csv(opt_path, index=False)



# In[17]:

today_proj.columns.values


# In[ ]:


