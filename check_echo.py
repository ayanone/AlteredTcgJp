import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from bulk_translate import collect_cards

# BTG (サポート{D}), WFM ({I}パッシブ), ROC ([[Completed]]) を確認
cards = collect_cards(['BTG', 'WFM', 'ROC'])

targets = {
    'BTG-004', 'BTG-162',        # {D} サポート
    'WFM-020', 'WFM-039',        # {I} パッシブ
    'ROC-032', 'ROC-036', 'ROC-018',  # [[Completed]]
}
for c in cards:
    if c['card_number'] in targets and c['rarity'] == 'C':
        print(f"{c['card_number']}-{c['rarity']}: {c['name']}")
        print(f"  {c['effect']}")
        print()
