import json, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

TARGET_SETS = ['CORE','ALIZE','BISE','CYCLONE','DUSTER','EOLE']
shown = {'V': False, 'M': False, 'O': False}

for s in TARGET_SETS:
    for root, dirs, files in os.walk(f'CARDS/EN/{s}'):
        for fn in files:
            if not fn.endswith('.json'):
                continue
            try:
                with open(os.path.join(root, fn), encoding='utf-8') as f:
                    data = json.load(f)
                effect = data.get('elements', {}).get('MAIN_EFFECT', '')
                cn = data.get('collectorNumber', '')
                for sym in ['V', 'M', 'O']:
                    key = '{' + sym + '}'
                    if not shown[sym] and key in effect:
                        snippet = effect[:250].replace('\xa0', ' ')
                        print(f'{key} example ({cn}):')
                        print(snippet)
                        print()
                        shown[sym] = True
            except Exception:
                pass
    if all(shown.values()):
        break
