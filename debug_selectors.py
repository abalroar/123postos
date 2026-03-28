"""Analisa o HTML salvo e encontra os seletores corretos para nome, horário e duração."""

from selectolax.lexbor import LexborHTMLParser

with open("logs/google_flights_raw.html", encoding="utf-8") as f:
    html = f.read()

parser = LexborHTMLParser(html)

# Containers de voo (seletores do fast-flights original)
containers = parser.css('div[jsname="IWWDBc"], div[jsname="YdtKid"]')
print(f"Containers encontrados: {len(containers)}")

if not containers:
    print("ERRO: nenhum container encontrado")
    raise SystemExit

# Pega o primeiro li do primeiro container
items = containers[0].css("ul.Rk10dc li")
print(f"Itens no primeiro container: {len(items)}")

if not items:
    print("ERRO: nenhum item encontrado")
    raise SystemExit

item = items[0]
print("\n=== TEXTO COMPLETO DO PRIMEIRO VOO ===")
print(item.text(strip=True)[:500])

print("\n=== TODOS OS SPANS com texto ===")
for span in item.css("span"):
    t = span.text(strip=True)
    classes = span.attributes.get("class", "")
    if t and len(t) < 80:
        print(f"  span.'{classes}' → '{t}'")

print("\n=== TODOS OS DIVS com texto ===")
for div in item.css("div"):
    t = div.text(strip=True)
    classes = div.attributes.get("class", "")
    jsname = div.attributes.get("jsname", "")
    if t and len(t) < 80 and len(classes) < 100:
        marker = f"[jsname={jsname}]" if jsname else ""
        print(f"  div.'{classes}'{marker} → '{t}'")

# Testa seletores específicos
print("\n=== TESTANDO SELETORES ===")
tests = [
    "div.sSHqwe.tPgKwe.ogfYpf span",  # nome original
    "span.mv1WYe div",                  # horário original
    ".YMlIz.FpEdX",                     # preço (funciona)
    "li div.Ak5kof div",               # duração original
    ".BbR8Ec .ogfYpf",                 # paradas original
    # novos candidatos
    "span[aria-label]",
    "div[aria-label]",
    "g-inner-card",
]
for sel in tests:
    try:
        nodes = item.css(sel)
        texts = [n.text(strip=True) for n in nodes if n.text(strip=True)]
        print(f"  '{sel}' → {texts[:5]}")
    except Exception as e:
        print(f"  '{sel}' → ERRO: {e}")
