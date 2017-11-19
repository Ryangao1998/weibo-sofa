#!/usr/bin/env python3

import http.cookies
import re
import time

import arrow
import bs4
import requests

import ws.conf
import ws.utils
from ws.logger import logger

# 1st group: original user id pattern
# 2nd group: content between ouid and mid, might contain
#            r'feedtype=\"top\"', the signature of a pinned status
# 3rd group: status id pattern
# 4th group: last path segment of the status URL (which should look like
#            http://weibo.com/<status_id>/<hash>
# 5th group: timestamp pattern (Javascript millisecond timstamp, 13
#            digits till year 2286)
EXTRACTOR = re.compile(r'ouid=(\d+)(.*?)mid=\\"(\d+)\\".*?href=\\"\\/\1\\/(\w+)\?.*?date=\\"(\d{13})\\"(.*?)class=\\"WB_feed_handle\\"')

SESSION = requests.Session()
SESSION.headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/55.0.2883.95 Safari/537.36',
}

def load_cookie(s):
    cookie = http.cookies.SimpleCookie()
    cookie.load(s)
    for key, morsel in cookie.items():
        SESSION.cookies.set(key, morsel.value, domain='weibo.com')
    load_cookie.has_been_run = True

load_cookie.has_been_run = False

def fetch(uid):
    if not load_cookie.has_been_run:
        raise RuntimeError('haven\'t provided weibo.com cookie with load_cookie')
    try:
        resp = SESSION.get(f'http://weibo.com/u/{uid}?is_all=1')
    except Exception:
        logger.warning('connection failed, retrying in 5...')
        time.sleep(5)
        return None
    if resp.status_code != 200:
        dumppath = ws.utils.dump(resp.text)
        logger.warning(f'got HTTP {resp.status_code}; response dumped into {dumppath}')
        return None
    return resp.text

# Returns a list of tuple of three ints, a str, and a bool:
#   (ouid, status_id, status_timestamp, url, repost)
def parse(html):
    statuses = []
    for ouid, filler1, sid, basename, timestamp_ms, filler2 in EXTRACTOR.findall(html):
        # Skip pinned status
        if r'feedtype=\"top\"' not in filler1:
            ouid = int(ouid)
            sid = int(sid)
            timestamp = int(timestamp_ms) // 1000
            url = f'http://weibo.com/{ouid}/{basename}'
            repost = r'\"WB_feed_expand\"' in filler2
            statuses.append((ouid, sid, timestamp, url, repost))
    return statuses

# Returns a tuple of two ints, a str, and a bool:
#   (status_id, status_timestamp, url, repost)
# or None if no original status is found.
#
# A warning is optionally issued when no original status is found. If
# warn_on_consecutive is True, only issue the warning if the same has
# happened in the last minute; this reduces unnecessary warnings when
# weibo.com errors out internally once in a while (I sometimes get a few
# nonsensical "她还没有发过微博" out of the thousands of responses I
# retrieve every hour).
def latest_status(uid, warn=True, warn_on_consecutive=True):
    uid = int(uid)
    html = fetch(uid)
    if html is None:
        return None
    statuses = parse(html)
    try:
        return next(filter(lambda s: s[0] == uid, statuses))[1:]
    except StopIteration:
        time_since_last_exception = time.time() - latest_status.last_exception_timestamp
        if warn and not (warn_on_consecutive and time_since_last_exception > 60):
            dumppath = ws.utils.dump(html)
            logger.warning(f'no original status found; response dumped into {dumppath}')
        latest_status.last_exception_timestamp = time.time()
        return None

latest_status.last_exception_timestamp = 0

# Returns a list of triplets of ints
#   (sid, cid, uid)
# representing the first screenful of latest comments to the status
# sid. cid is the comment id, and uid is the user id of the commenter.
#
# A None return value indicates failure.
def status_comments(sid):
    sid = int(sid)
    if not load_cookie.has_been_run:
        raise RuntimeError('haven\'t provided weibo.com cookie with load_cookie')
    try:
        # 'filter' could also be 'hot' for fetching popular comments
        resp = SESSION.get(f'http://weibo.com/aj/v6/comment/big?id={sid}&filter=all&from=singleWeiBo')
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        soup = bs4.BeautifulSoup(resp.json()['data']['html'], 'html.parser')
    except Exception:
        return None

    comments = []
    for comment_node in soup.find_all(attrs={'node-type': 'root_comment'}):
        cid = int(comment_node['comment_id'])
        commenter_avatar = comment_node.find('img', attrs={
            'usercard': lambda value: value and value.startswith('id=')
        })
        uid = int(commenter_avatar['usercard'][3:])
        comments.append((sid, cid, uid))
    return comments

load_cookie(ws.conf.cookies)
