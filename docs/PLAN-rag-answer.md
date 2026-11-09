---
id: plan-rag-answer
summary: Plán české ověřené odpovědi nad fastmcp-rag-timer. Jen návrh, bez implementace.
parent: index
children: []
---

# Plán: česká odpověď z RAG

Web dnes ukáže syrové hity. Cíl je stejný postup jako TesterVerifier: rozšířit dotaz, ověřit ho proti katalogu a fulltextu, oddělit šum a nechat model složit českou odpověď s citacemi, GPS a obrázky. Tohle je návrh navazující na query page z PR #2. Kód se v tomto kroku nemění.

## overview

Tok zůstává ten z [architecture.md](architecture.md): prohlížeč mluví jen s procesem v tomto repozitáři, ten drží `MCP_API_TOKEN` a volá `https://rag-timer-00001-production.up.railway.app/mcp/`. LLM krok patří do stejného procesu. Klíč modelu do prohlížeče nepatří.

```text
prohlížeč
  -> POST /api/answer
    -> expand (varianty dotazu)
    -> MCP klient (search, katalog, fulltext) paralelně
    -> parsery a klasifikace šumu
    -> LLM (jen JSON faktů)
  -> karty, mapa, obrázky, QA panel
```

MCP server a jeho embeddingy (Novita / FastEmbed) se nemění. Aplikace z nich neskládá věty.

## architektura

Proxy už běží. `HttpMcpClient` v [src/rag_frontend/mcp_client/session.py](../src/rag_frontend/mcp_client/session.py) otevírá Streamable HTTP se stateless voláním a hlavičkou `Authorization: Bearer`. [src/rag_frontend/redact.py](../src/rag_frontend/redact.py) maže hodnotu tokenu z textových odpovědí a z logů. Nová trasa `/api/answer` použije stejného klienta. Druhá veřejná služba není potřeba.

LLM krok je nové odchozí HTTPS volání z téhož procesu, až po složení JSON faktů. Model nedostane bearer token, surový katalog ani celý fulltext. Dostane jen vybrané objekty, homonyma, šum a chybějící obrázky.

### co je v repu a na Railway

| Místo | Nález |
| --- | --- |
| Tento repozitář | Žádný LLM klient, žádný `OPENAI` / `ANTHROPIC` / `NOVITA` klíč. |
| Služba `fastmcp-rag-timer-frontend` | Z vlastních proměnných jen `MCP_API_TOKEN` plus proměnné, které doplňuje Railway. Chat klíč tam není. |
| `FastMCP-aggregator` | Bez LLM klíče. |
| `lunar-mcpx` | `PIPEWORX_API_KEY` a `AUTH_KEY`. To není klíč k chat modelu pro tuhle stránku. |
| `MCP-inspectoriat` | `RAG_API_TOKEN`, Google OAuth, tajemství inspektoru. Chat klíč to není. |

Doporučení: nová runtime proměnná jen na frontendu, sealed, aby ji nešlo vypsat zpět. Jméno a poskytovatel ať zvolí Marek (otázky dole). Do kódu se hodnota nezapíše. Stejný redaktor jako u `MCP_API_TOKEN` pokryje i tento klíč.

Navržený tvar, dokud Marek neřekne jinak: OpenAI-kompatibilní chat (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`). Hodí se na OpenAI i na Novita chat, ale Novita klíč z RAG serveru na téhle službě není a nesmí se sem kopírovat do gitu.

## soubory

Měnit:

| Soubor | Změna |
| --- | --- |
| [src/rag_frontend/api/routes.py](../src/rag_frontend/api/routes.py) | `POST /api/answer`. Stávající `/api/search` a katalog nechat pro QA panel. |
| [src/rag_frontend/config.py](../src/rag_frontend/config.py) | URL, model a klíč LLM, TTL cache. Bez klíče trasa vrátí 503, stránka se načte. |
| [src/rag_frontend/web/index.html](../src/rag_frontend/web/index.html) | Jedna stránka: odpověď, karty, mapa, QA panel. |
| [src/rag_frontend/web/app.js](../src/rag_frontend/web/app.js) | Vykreslení odpovědi. Žádný MCP ani LLM klíč. |
| [src/rag_frontend/web/styles.css](../src/rag_frontend/web/styles.css) | Karty a odlišení centroidu. |
| [docs/architecture.md](architecture.md) | Až s implementací, ne v tomto PR. |

Vytvořit:

| Soubor | Účel |
| --- | --- |
| `src/rag_frontend/answer/expand.py` | Varianty dotazu. |
| `src/rag_frontend/answer/retrieve.py` | Paralelní search, katalog, cílený fulltext. Měří latence. |
| `src/rag_frontend/answer/parsers.py` | Bloky search, building-hints, AKON, obce.json, OCR. |
| `src/rag_frontend/answer/classify.py` | Relevantní / homonymum / šum / chybí obrázek. |
| `src/rag_frontend/answer/synthesize.py` | Volání LLM nad JSON. |
| `src/rag_frontend/answer/prompt.py` | Česká šablona. |
| `src/rag_frontend/answer/cache.py` | Katalog a fulltext v paměti procesu. |
| `tests/test_answer_parse.py` | Parsery a klasifikace na úryvcích ze SPEC. |
| `tests/test_answer_route.py` | Trasa s podvrženým MCP i LLM. |

Existující [src/rag_frontend/mcp_client/parse.py](../src/rag_frontend/mcp_client/parse.py) umí blok `Source` / `Content` a veřejná pole katalogu. Nové parsery ho doplní, nenahradí náhledy v katalogu.

## api

`POST /api/answer`

```json
{ "query": "co ví server o lokalitě Vršovice a secesi zde" }
```

Odpověď 200:

| Pole | Smysl |
| --- | --- |
| `answer_markdown` | Český text podle šablony. |
| `objects` | Karty: název, adresa, rok, lidé, confidence, zdroje, gps, `gps_kind` (`point` nebo `centroid`), `doc_id`, `image_ids`. |
| `noise` | Homonyma, dokumenty bez zmínky, chyba řazení. |
| `images` | `{image_id, doc_id, caption}` pro proxy. |
| `debug` | Varianty, latence, počty hitů. Bez tokenu a bez LLM klíče. |

Prázdný dotaz: 400, bez volání ven. Chybí `MCP_API_TOKEN` nebo LLM klíč: 503 s větou, že údaj není nastaven, ne s jeho hodnotou.

Obrázky dál přes existující `GET /api/images/{image_id}` (`get_image`). Nová cesta `/api/rag/image/{doc_id}/{image_id}` není potřeba: server hledá `image_id` globálně a proxy token nepřidává do URL.

## postup odpovědi

1. **Rozšíření.** První verze je pravidlo, ne model: celá entita, entita + téma, entita + typ stavby. Sousední čtvrť a historický název (`Wrschowitz`) až z malého slovníku aliasů, který Marek dodá. LLM na expanzi je až druhý krok, ať výsledek nezávisí na modelu dvakrát.
2. **Search.** `search_domain_knowledge` s `limit=8` pro každou variantu, `asyncio.gather`. Dedup podle `(doc_id, page, image_id, hash textu)`.
3. **Katalog.** `list_documents` z cache. U ne-AKON dokumentů s `chunk_count > 0` `get_document_text` a regex entity (`Vršovic|Wrschowitz|Vršov` a aliasy z kroku 1). Kolem zásahu ±150 znaků. AKON (`akon_*.pdf`, řádově 700 souborů) se fulltextem neprochází; berou se jen search hity.
4. **Parsery.**
   - `*-building-hints.txt`: řez na `\n(?=## )`, pole `Adresa`, `Souřadnice`, `Confidence`, `link_ids`, `Poznámka`, `Zdroje`, `Obrázky`. Sekce bez entity v adrese nebo poznámce pryč.
   - AKON: `Misto`, `Rok`, `Souradnice`, `akon_id`, `license`, `iiif`. Stejné souřadnice u různých `Misto` v jednom městě = `gps_kind=centroid`.
   - `obce.json`: `name`, `id`, `nationalCode`. Prefix `id` (CZ08 Moravskoslezský, CZ04 Ústecký, …) proti očekávanému kraji. Jiný kraj = homonymum.
   - `pdf_image`: OCR text plus `image_id`. Bayty jen přes proxy.
5. **Třídy.** `relevant` (entita ve správném kontextu), `homonym`, `noise` (entita v obsahu není; první hit bez entity = poznámka „chyba řazení“), `no_image` (sekce bez `Obrázky`).
6. **Syntéza.** Jedno chat volání. Když LLM klíč není, API vrátí karty a šum bez složené věty, ať se dá ověřit retrieval.

Latence ze SPEC: studený search až desítky sekund, pět variant sekvenčně asi 8–11 s. Varianty musí běžet paralelně. Limit souběhu ať je malý (například 3), ať server nezavalíme.

## prompt

Systémová část, česky, pevná:

1. Piš jen z dodaného JSON. Co v JSON není, nenapiš.
2. Každý fakt má `doc_id` a název souboru. Bez zdroje větu vynech.
3. Šablona: tučná první věta (kolik toho server ví a jak je to jisté); odrážka na objekt (název, čp., adresa, rok, stavitel nebo architekt, confidence, markdown odkaz na zdroj, **GPS**); odstavec o obrázcích (má / nemá, kolekce, centroid versus bod); odstavec „Šum“; závěr a jeden další krok.
4. Homonymum nepleť do hlavních objektů.
5. Nevymýšlej URL obrázků. `image_id` vezmi z JSON.

Uživatelská část je jen JSON: `query`, `variants`, `objects`, `homonyms`, `noise`, `missing_images`, `absent_corpora` (které fulltexty entitu nemají).

Příklad tónu, ne text k vložení do kódu: odpověď o pražských Vršovicích z building-hints (Žitomírská, confidence 0,45, GPS 50.068522, 14.4555164), bez AKON pohlednic, se šumem dvou jiných Vršovic v `obce.json` a s reklamou z Pardubické výstavy na prvním místě search.

## ui

Jedna stránka, formulář nahoře.

- Pod ním složená odpověď (markdown z serveru, vykreslená bez vkládání HTML z modelu).
- Karty objektů: adresa, rok, confidence, zdroje, GPS. Klik otevře fulltext jako dnes.
- Mapa Leaflet + dlaždice OSM. `point` jiná barva než `centroid`. Bez vlastního tile serveru.
- Náhledy přes `/api/images/{image_id}`. Karta bez `image_id` řekne „obrázek nemá“.
- Sbalený panel Debug/QA: varianty, latence každého MCP a LLM volání, počty, označený šum, raw hity. Bez hlavičky Authorization.

Současný syrový search může zůstat schovaný v tom panelu, ať se neztratí dnešní chování.

## cache

V paměti procesu:

| Klíč | Co | Invalidace |
| --- | --- | --- |
| `catalog` | výsledek `list_documents` | TTL, výchozí 10 minut, nebo prázdný seznam po chybě se neukládá |
| `fulltext:{doc_id}` | text ne-AKON dokumentu, který se už četl | stejný TTL |

Webhook na ingest není. Po nasazení je cache prázdná. Railway má u služby zapnutý spánek a jedna replika v `europe-west4`, takže cache nepřežije uspání a mezi replikami se nesdílí. První dotaz po probuzení znovu stáhne katalog. Fulltext všech ne-AKON dokumentů při každém dotazu nečíst: jen ty, jejichž název nebo search hit naznačuje korpus mimo AKON, plus cílený regex. Jinak 700 pohlednic a stovky fulltextů překročí rozumnou latenci.

## testy

Bez sítě. MCP klient i LLM klient se nahradí záznamem.

- building-hints úryvek Žitomírská → jedna karta, confidence 0,45, GPS, `no_image`.
- AKON se stejnými souřadnicemi u dvou `Misto` → `centroid`.
- `obce.json` id `CZ0805…` a `CZ0424…` při očekávané Praze → homonymum.
- Search hit bez entity na prvním místě → `noise` a poznámka o řazení.
- Dedup dvou stejných bloků.
- `POST /api/answer` složí markdown z podvrženého modelu a v těle ani v logu není zkušební token ani LLM klíč.
- Prázdný dotaz nevolá MCP ani LLM.
- Katalog z cache nespustí druhé `list_documents`.

Živý Vršovice dotaz proti produkci není součást testů. Je to ruční kontrola po nasazení.

## rizika

- První studený search může trvat desítky sekund. UI musí ukázat, že se čeká, a nesmí otevřít druhé stejné okno samo.
- Fulltext „všeho ne-AKON“ při stovkách souborů je drahý. Bez omezení korpusu plán nesplní latenci ze SPEC.
- Model může hladce domyslet jistotu. Šablona a test na citaci `doc_id` to jen snižují.
- Prefixy `CZ` krajů jsou dohoda, ne kontrola souřadnic. Špatná tabulka prefixů označí Prahu za šum nebo naopak.
- Centroid AKON je heuristika „stejné souřadnice, různé místo“. Jeden snímek ve městě se může tvářit jako přesný bod.
- MIME z `get_image` nemusí sedět na bajty (JPEG pod `image/png`). Prohlížeč to někdy stejně zobrazí. Plán to neopravuje na RAG serveru.
- Spánek služby smaže cache a přidá studený start k studenému search.
- Redakce musí dostat i LLM klíč, jinak chybová hláška poskytovatele může klíč vrátit do stránky.

## otevřené otázky

Otázky jsou i ve zprávě k tomuto plánu. Tady je kanonický seznam.

1. Který chat model a který účet? OpenAI, Novita chat, nebo něco jiného? Jak se jmenuje runtime proměnná?
2. Smí expanze použít LLM, nebo první verze jen pravidla a alias slovník?
3. Kdo dodá aliasy (Wrschowitz, sousední čtvrti) a pro které entity? Prázdný slovník neumí historické názvy.
4. Jaký kraj je „správný“, když uživatel nenapíše Prahu? Vršovice bez „Praha 10“ jsou v datech tři.
5. Fulltext: všechny ne-AKON dokumenty při každém dotazu, nebo jen korpusy mimo pohlednice, které Marek vyjmenuje?
6. Má syrový search na stránce zůstat, nebo ho nahradí jen odpověď a QA panel?
7. Leaflet a OSM dlaždice z veřejného serveru jsou přijatelné?
8. TTL cache 10 minut stačí, nebo se má cache rušit jen restartem?
9. Limit souběžných `search_domain_knowledge` (návrh 3) a strop fulltextů na jeden dotaz?
10. Když LLM klíč chybí, stačí karty bez složené věty, nebo se má formulář schovat?
