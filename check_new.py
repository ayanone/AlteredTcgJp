import sys, csv
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
rows = list(csv.DictReader(open('AlteredTcgJp.csv', encoding='utf-8')))
targets = {'BTG-154','BTG-157','BTG-163','BTG-175','BTG-159'}
for r in rows:
    if r['カード番号'] in targets:
        print(f"{r['カード番号']}-{r['レアリティ']}: {r['日本語名']}")
        print(f"  {r['能力'][:100]}")
        print()
