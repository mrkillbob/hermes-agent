"""Public endpoint conformance only. No credentials, OAuth transactions or retries."""
import hashlib,json,os,pathlib,re,subprocess,tempfile,time
ORIGIN='https://mikes-mac-mini.taile5f8a4.ts.net:10000'
RESOURCE=ORIGIN+'/mcp';PRM=ORIGIN+'/.well-known/oauth-protected-resource/mcp';AS=ORIGIN+'/.well-known/oauth-authorization-server'
INIT={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'public-response-conformance','version':'1'}}}
KEEP={'content-type','content-length','transfer-encoding','connection','www-authenticate','location','allow'}
META={'resource','authorization_servers','issuer','authorization_endpoint','token_endpoint','token_endpoint_auth_methods_supported','code_challenge_methods_supported','scopes_supported'}
def selected_headers(raw):
    # curl can record informational/proxy response blocks. Select final HTTP block.
    blocks=re.split(br'\r?\n\r?\n',raw);out={}
    for block in blocks:
        if not block.startswith(b'HTTP/'):continue
        out={}
        for line in block.splitlines()[1:]:
            k,sep,v=line.partition(b':')
            if sep and k.decode('ascii','ignore').lower() in KEEP:out[k.decode('ascii').lower()]=v.strip().decode('utf-8','replace')
    return out

def main():
    results=[];failures=[]
    with tempfile.TemporaryDirectory(prefix='public-mcp-read-') as tmp:
        root=pathlib.Path(tmp);env={'PATH':'/usr/bin:/bin','HOME':tmp,'CURL_HOME':tmp,'LANG':'C.UTF-8'}
        version=subprocess.run(['/usr/bin/curl','-q','--version'],env=env,capture_output=True,text=True,check=True,timeout=10).stdout
        modes=['http1.1']+(['http2'] if any('HTTP2' in line for line in version.splitlines() if line.startswith('Features:')) else [])
        (root/'initialize.json').write_text(json.dumps(INIT,separators=(',',':')))
        for mode in modes:
            chain=[('POST',RESOURCE,401),('GET',RESOURCE,405)]
            for index,(method,url,want) in enumerate(chain):
                row=probe(root,env,mode,index,method,url,want);results.append(row)
                if not row['passed']:failures.append({'mode':mode,'step':index});break
            else:
                post=results[-2];challenge=post['headers'].get('www-authenticate','')
                if not re.search(r'(?:^|[ ,])resource_metadata="'+re.escape(PRM)+r'"',challenge):
                    failures.append({'mode':mode,'step':'advertised_prm_mismatch'});continue
                row=probe(root,env,mode,2,'GET',PRM,200);results.append(row)
                if not row['passed'] or row.get('metadata',{}).get('resource')!=RESOURCE or row.get('metadata',{}).get('authorization_servers')!=[ORIGIN]:
                    failures.append({'mode':mode,'step':'prm_identity'});continue
                row=probe(root,env,mode,3,'GET',AS,200);results.append(row)
                m=row.get('metadata',{})
                if not row['passed'] or m.get('issuer')!=ORIGIN or m.get('token_endpoint')!=ORIGIN+'/oauth/token' or m.get('authorization_endpoint')!=ORIGIN+'/oauth/authorize' or m.get('token_endpoint_auth_methods_supported')!=['none'] or 'S256' not in m.get('code_challenge_methods_supported',[]):failures.append({'mode':mode,'step':'as_identity'})
        out={'endpoint':RESOURCE,'curl_version':version.splitlines()[0],'http2_available':'http2' in modes,'requested_modes':modes,'results':results,'failures':failures,'scope':'Unauthenticated public HTTP only; no redirects/retries/login/grant/token/callback/model/task.','limits':'curl successful body transfer, not independent TLS close_notify proof. --http2 may negotiate HTTP1.1; actual http_version is recorded. No forced HTTP2 or dependency install.'}
        print(json.dumps(out,indent=2));return 1 if failures else 0

def probe(root,env,mode,index,method,url,want):
    headers=root/f'{mode}-{index}.headers';body=root/f'{mode}-{index}.body'
    cmd=['/usr/bin/curl','-q','--'+mode,'--silent','--show-error','--proto','=https','--connect-timeout','10','--max-time','20','--request',method,'--header','Accept: application/json, text/event-stream','--header','Authorization:','--header','Cookie:','--dump-header',str(headers),'--output',str(body),'--write-out','%{json}']
    if method=='POST':cmd+=['--header','Content-Type: application/json','--data-binary','@'+str(root/'initialize.json')]
    else:cmd+=['--header','MCP-Protocol-Version: 2024-11-05']
    cmd += [url]
    try:r=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=25)
    except subprocess.TimeoutExpired:return {'mode':mode,'method':method,'url':url,'passed':False,'error':'owned_curl_timeout'}
    try:metrics=json.loads(r.stdout)
    except json.JSONDecodeError:metrics={}
    data=body.read_bytes() if body.exists() else b'';h=selected_headers(headers.read_bytes()) if headers.exists() else {}
    row={'mode':mode,'method':method,'url':url,'curl_exit':r.returncode,'status':metrics.get('http_code'),'http_version':metrics.get('http_version'),'time_connect':metrics.get('time_connect'),'time_starttransfer':metrics.get('time_starttransfer'),'time_total':metrics.get('time_total'),'body_bytes':len(data),'body_sha256':hashlib.sha256(data).hexdigest(),'headers':h}
    length=h.get('content-length');length_ok=length is None or (length.isdigit() and int(length)==len(data))
    row['passed']=r.returncode==0 and row['status']==want and length_ok and 'location' not in h and h.get('content-type','').split(';')[0]=='application/json'
    if want==200 and row['passed']:
        try:
            m=json.loads(data);row['metadata']={k:v for k,v in m.items() if k in META}
        except (ValueError,AttributeError):row['passed']=False
    return row
if __name__=='__main__':raise SystemExit(main())
