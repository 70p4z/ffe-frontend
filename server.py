import os
import time
from flask import Flask, render_template, request, make_response
import threading
import datetime
import requests
import logging
import argparse
import re
from http.client import HTTPConnection
import json
import sys
import html
from collections import defaultdict
from collections import OrderedDict
import traceback
from urllib.parse import unquote
import binascii

VERSION="1"

TIMEOUT_S=600

LOGLEVEL = os.environ.get('LOGLEVEL', 'INFO').upper()
LOGFORMAT = '%(asctime)s %(levelname)s %(threadName)s %(funcName)s %(message)s'
logging.basicConfig(format=LOGFORMAT)
log = logging.getLogger('')
log.setLevel(LOGLEVEL)
# also debug the http module
if LOGLEVEL == "DEBUG":
  HTTPConnection.debuglevel = 1

parser = argparse.ArgumentParser(description='Version ' + str(VERSION))
parser.add_argument('-w', '--webdir', help='Webdir when hosting in subfolder', default="/ffe")
(args, unparsed) = parser.parse_known_args()
cl_args = vars(args)

# init Flask
app = Flask(__name__)

# Basic config with security for forms and session cookie
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['CSRF_ENABLED'] = True
app.config['SECRET_KEY'] = 'thisismyscretkey'
app.config['TEMPLATES_AUTO_RELOAD'] = True
# ----------------------------------------------------------------------------
# UPSTREAM 
# ----------------------------------------------------------------------------
headers = {
'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; rv:102.0) Gecko/20100101 Firefox/102.0' ,
'X-Requested-With': 'XMLHttpRequest' ,
'Accept-Language': 'en-US,en;q=0.5'
}
upstream_stem='https://dirigeant.escrime-ffe.fr'

def upstream_login(upstream_session):
  upstream_session.personnes=[]
  upstream_session.engagements=[]
  upstream_session.structure_id=0
  # login to the interface
  # Grab the session
  r = upstream_session.get(f'{upstream_stem}/auth/login', headers=headers)
  m = re.search(r'name="csrf-token"\s+content="(\w+)"', r.text)
  if(r.status_code != 200):
    raise BaseException("Can't contact remote authentication service")
  try:
    csrf_token=m.group(1)
  except:
    raise BaseException("Can't retrieve authentication token")

  # authenticate
  formdata= {}
  formdata["_token"]= csrf_token
  formdata["username"]= upstream_session.username
  formdata["password"]= upstream_session.password
  r = upstream_session.post(f'{upstream_stem}/auth/login', data=formdata, headers=headers, allow_redirects=True)
  if(r.status_code != 200):
    raise BaseException("Username/password are invalid")
  m = re.search(r'currentStructureAuth\s*=\s*{"id"\s*:\s*(\w+),', r.text)
  try:
    upstream_session.structure_id=m.group(1)
    upstream_session.login_time=time.time()
  except:
    raise BaseException("Can't retrieve structure id")

def upstream_populate_personnes(upstream_session):
  today = datetime.date.today()
  season = today.year
  # from september, consider next year season
  if (today.month >= 9):
    season+=1
  
  # retrieve the whole list of club affiliates
  start=0
  length=25
  total=-1
  upstream_session.personnes=[]
  while total == -1 or start < total:
    r = upstream_session.get(f'{upstream_stem}/structures/fiche/{upstream_session.structure_id}/licencies/ajax?start={start}&length={length}&search%5Bvalue%5D=&search%5Bregex%5D=false&filtres%5Bpersonne%5D=&filtres%5Bsexe%5D=&filtres%5Bsaison%5D={season}&filtres%5Betat%5D=&filtres%5Bstructure%5D={upstream_session.structure_id}', headers=headers)
    j = r.json()
    if total == -1:
      total = j['total']
    start += len(j['data'])
    upstream_session.personnes=upstream_session.personnes+j['data']
    sys.stdout.write(f'\rloading personnes: {start}/{total} ({round(start*100/total)}%)')

  # compute discpline as described in engagements
  for val in upstream_session.personnes:
    val['discipline_code'] = []
    if 'discipline' in val:
      if val['discipline'].find('Fleuret') !=1:
        val['discipline_code'].append('FLE')
      if val['discipline'].find('Epée') !=1:
        val['discipline_code'].append('EPE')
      if val['discipline'].find('Sabre\\') !=1:
        val['discipline_code'].append('SAB')
  log.debug(upstream_session.personnes)
  log.debug(len(upstream_session.personnes))
  log.debug(total)

def upstream_populate_engagements(upstream_session, start=0):
  today = datetime.date.today()
  season = today.year
  # from september, consider next year season
  if (today.month >= 9):
    season+=1
  
  engagement_status = 3 # open for subscription
  
  # retrieve the whole list of club affiliates
  filtr=f'columns[0][data]=date_debut&columns[0][name]=debut_competition&columns[0][searchable]=true&columns[0][orderable]=true&columns[0][search][value]=&columns[0][search][regex]=false&order[0][column]=0&order[0][dir]=asc&filtres[reset]=true&filtres[ville]=&filtres[niveau]=&filtres[discipline]=0&filtres[sexe]=0&filtres[categorie]=0&filtres[ind_equip]=0&filtres[intitule]=&filtres[structure]=&filtres[typeCompetition]=&filtres[niveauCompetition]=&filtres[saison]=&filtres[statut]={engagement_status}&_={round(time.time()*1000)}'
  try:
    #start=0
    length=100
    total=-1
    upstream_session.engagements=[]
    while total == -1 or start < total:
      r = upstream_session.get(f'{upstream_stem}/engagement/ajax?start={start}&length={length}&{filtr}', headers=headers)
      j = r.json()
      if total == -1:
        total = j['total']
      start += len(j['data'])
      upstream_session.engagements=upstream_session.engagements+j['data']
      sys.stdout.write(f'\rloading engagements: {start}/{total} ({round(start*100/total)}%)')
      # avoid too long loading
      if start >= 200:
        break;
    log.debug(upstream_session.engagements)
    log.debug(len(upstream_session.engagements))
    log.debug(total)

    # group by by date + commune
    eng=defaultdict(list)
    for e in upstream_session.engagements:
      # refactor date for sorting to work nicely
      meta_date = datetime.datetime.strptime(e['date_debut'], '%d/%m/%Y').strftime('%Y/%m/%d')
      e['meta_date'] = meta_date
      e['sexe']=e['sexe'][0:1]
      eng[f"{meta_date} {e['commune']}"].append(e)

    log.debug(eng)

    # sort by date + commune, and create meta_id for sub/unsub
    seng=OrderedDict()
    meta_id=1
    for key in sorted(eng.keys()):
      val = eng[key]
      seng[key] = val
      for v in val:
        v['meta_id'] = str(meta_id)
      meta_id+=1
  except:
    traceback.print_exc()
    seng=OrderedDict()

  # transform again for easier indexation to play in subscription
  upstream_session.engagements={}
  for key, val in seng.items():
    upstream_session.engagements[val[0]['meta_id']] = val

def upstream_subscription_state(upstream_session, engagement_id):
  r = upstream_session.get(f'{upstream_stem}/engagement/engagement/{engagement_id}', headers=headers)
  m = re.search(r':engages="([^\n]*)"\n', r.text)
  engages={}
  try:
    subs=json.loads(html.unescape(m.group(1)))
    # for each person of the structure, describe its state
    for p in upstream_session.personnes:
      for e in subs:
        if e['personne_id'] == p['personne_id']:
          # a personne can be subscribing to multiple engagement (allw merging dict in the caller)
          engages[p['personne_id']] = {'sub_id': e['id'], 'eng_id': engagement_id }
  except:
    raise BaseException(f"Can't retrieve list of subscribers for engagement {engagement_id}")
  return engages

def upstream_subscribe(upstream_session, engagement_id, personne_id):
  data={}
  data["engage_id"]= int(personne_id, 0)
  data["num_equipe"]=None
  #data= {"mimeType": "application/json;charset=utf-8", "params": [], "text": json.dumps(data)}
  hdrs = dict(headers.items())
  hdrs['Referer'] = f'{upstream_stem}/engagement/engagement/{engagement_id}'
  hdrs['X-XSRF-TOKEN'] = unquote(upstream_session.cookies['XSRF-TOKEN'])
  r = upstream_session.post(f'{upstream_stem}/engagement/engage/{engagement_id}/{upstream_session.structure_id}', json=data, headers=hdrs, allow_redirects=True)
  if r.status_code != 200:
    log.info(r.text)
    raise BaseException("Subscription failed")

def upstream_unsubscribe(upstream_session, engagement_id, subscription_id):
  hdrs = dict(headers.items())
  hdrs['Referer'] = f'{upstream_stem}/engagement/engagement/{engagement_id}'
  hdrs['X-XSRF-TOKEN'] = unquote(upstream_session.cookies['XSRF-TOKEN'])
  r = upstream_session.post(f'{upstream_stem}/engagement/delete-engage/{engagement_id}/{upstream_session.structure_id}/{subscription_id}', headers=hdrs, json=None, allow_redirects=True)
  if r.status_code != 200:
    log.info(r.text)
    raise BaseException("Unsubscription failed")

# ----------------------------------------------------------------------------
# WEB ENDPOINTS
# ----------------------------------------------------------------------------
@app.route("/")
def root():
  session = request.cookies.get('ffesession')
  if session is None or not session in upstream_sessions:
    return render_template('login.html', webdir=args.webdir)
  upstream_session = upstream_sessions[session]
  # relogin to ensure refreshed login
  if time.time() - upstream_session.login_time > TIMEOUT_S:
    upstream_login(upstream_session)
  return render_template('list_engagements.html', webdir=args.webdir, engagements=upstream_session.engagements)

@app.route("/logout")
def logout():
  session = request.cookies.get('ffesession')
  if session is None or not session in upstream_sessions:
    return render_template('login.html', webdir=args.webdir)
  # wipe server side
  if session in upstream_sessions:
    del upstream_sessions[session]
  # wipe client side
  resp = make_response(render_template('login.html', webdir=args.webdir))
  resp.set_cookie('ffesession', "")
  return resp

@app.route("/login")
def login():
  session = request.cookies.get('ffesession')
  #if not session is None:
  #  return root()  
  if not "username" in request.args or not "password" in request.args:
    return render_template('login.html', webdir=args.webdir)
  #create a new session
  session = binascii.hexlify(os.urandom(32)).decode('utf8')
  upstream_session=requests.Session()
  # store given credentials
  upstream_session.username = request.args['username']
  upstream_session.password = request.args['password']
  upstream_login(upstream_session)
  upstream_populate_personnes(upstream_session)

  if not os.path.exists("cached-engagements.json"):
    upstream_populate_engagements(upstream_session)
    #d = open("cached-engagements.json", "w+")
    #d.write(json.dumps(upstream_session.engagements))
    #d.close()
  else:
    d = open("cached-engagements.json", 'r')
    upstream_session.engagements = json.loads(d.read())
    d.close()

  upstream_sessions[session] = upstream_session
  resp = make_response(render_template('list_engagements.html', webdir=args.webdir, engagements=upstream_session.engagements))
  resp.set_cookie('ffesession', session)
  return resp

@app.route("/engagement/<meta_id>")
def engagement(meta_id):
  session = request.cookies.get('ffesession')
  if session is None or not session in upstream_sessions:
    return render_template('login.html', webdir=args.webdir)

  upstream_session = upstream_sessions[session]

  # retrieve list of engagement for that meta engagement (aggregation)
  engagements = upstream_session.engagements[meta_id]
  engages = {}
  for e in engagements:
    engages[e['id']] = upstream_subscription_state(upstream_session, e['id']) 

  # create a personne array, with levels that matchs the engagement discipline, sexe, categorie
  personnes=[]
  for p in upstream_session.personnes:
    pers = {}
    pers['personne_id'] = p['personne_id']
    pers['nom'] = p['nom']
    pers['prenom'] = p['prenom']
    pers['categorie'] = p['categorie_age'].upper()
    pers['sexe'] = p['sexe'][0:1]
    pers['subs'] = []
    log.debug(f"{pers['prenom']} {p['discipline_code']}")
    for e in engagements:
      if p['categorie_age'].upper().startswith(e['categorie']):
        # sometimes the sexe in competition contains BOTH M and F
        if e['sexe'].find(p['sexe'].upper()[0:1])!=-1:
          # check if discpline matches
          if e['discipline_code'].upper() in p['discipline_code']:
            # retrieve the subscription id if already subscribed to that engagement (to unsubscribe)
            i = ""
            if p['personne_id'] in engages[e['id']]:
              i = engages[e['id']][p['personne_id']]['sub_id']
            pers['subs'].append({ 'id': e['id'], 'categorie': e['categorie'], 'discipline': e['discipline_code'], 'sub_id': i})
    personnes.append(pers)
  # sort by name
  personnes=sorted(personnes, key=lambda x: x['prenom'])
  return render_template('engagement.html', webdir=args.webdir, meta_id=meta_id, personnes=personnes, engagements=engagements)

@app.route("/subscribe/<engagement_id>/<personne_id>")
def subscribe(engagement_id, personne_id):
  session = request.cookies.get('ffesession')
  if session is None or not session in upstream_sessions:
    return render_template('login.html', webdir=args.webdir)

  upstream_session = upstream_sessions[session]
  try:
    upstream_subscribe(upstream_session, engagement_id, personne_id)
  except:
    traceback.print_exc()
  return ('', 204)

@app.route("/unsubscribe/<engagement_id>/<subscription_id>")
def unsubscribe(engagement_id, subscription_id):
  session = request.cookies.get('ffesession')
  if session is None or not session in upstream_sessions:
    return render_template('login.html', webdir=args.webdir)

  upstream_session = upstream_sessions[session]
  try:
    upstream_unsubscribe(upstream_session, engagement_id, subscription_id)
  except:
    traceback.print_exc()
  return ('', 204)
  
# ----------------------------------------------------------------------------
# ENTRY POINT
# ----------------------------------------------------------------------------
# TODO: create a new one per session connecting to the flask, with a cookie for each
upstream_sessions={}

# run frontend with dev server
app.run(host='0.0.0.0', port=8080, threaded=True)
