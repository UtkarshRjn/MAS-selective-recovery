import hashlib, json, sys, datetime, os
F = 'freeze_hashes.json'
d = json.load(open(F)) if os.path.exists(F) else {'stages': []}
stage, files = sys.argv[1], sys.argv[2:]
d['stages'].append({'stage': stage, 'time_local': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
                    'files': {f: hashlib.sha256(open(f, 'rb').read()).hexdigest() for f in files}})
json.dump(d, open(F, 'w'), indent=1); print(json.dumps(d['stages'][-1], indent=1))
