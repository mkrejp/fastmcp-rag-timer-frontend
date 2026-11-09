---
id: plan-rag-answer
summary: Plán české ověřené odpovědi. Novita jen na syntézu, zbytek v kódu, celý web za Google přihlášením.
parent: index
children: []
---

# Plán: česká odpověď z RAG

Web dnes ukáže syrové hity. Cíl je stejný postup jako TesterVerifier: rozšířit dotaz, ověřit ho proti katalogu a fulltextu, oddělit šum a nechat model složit českou odpověď s citacemi, GPS a obrázky. Tohle je návrh navazující na query page z PR #2. Kód se v tomto kroku nemění.

## overview

Tok zůstává ten z [ARCHITECTURE.md](ARCHITECTURE.md): prohlížeč mluví jen s procesem v tomto repozitáři, ten drží `MCP_API_TOKEN` a volá `https://rag-timer-00001-production.up.railway.app/mcp/`. LLM krok patří do stejného procesu. Klíč modelu do prohlížeče nepatří.

```text
prohlížeč (jen s Google session)
  -> POST /api/answer
    -> aliasy a regexy (žádné LLM)
    -> MCP klient
    -> parsery, dedup, šum
    -> 0 nebo 1 volání Novita chat
  -> karty, mapa, obrázky, QA panel (tokeny)
```

MCP server a jeho embeddingy se nemění. Search na něm zůstává FastEmbed. Chat je jediné nové Novita volání a je až na frontendu.

## architektura

Proxy už běží. `HttpMcpClient` v [src/rag_frontend/mcp_client/session.py](../src/rag_frontend/mcp_client/session.py) otevírá Streamable HTTP se stateless voláním a hlavičkou `Authorization: Bearer`. [src/rag_frontend/redact.py](../src/rag_frontend/redact.py) maže hodnotu tokenu z textových odpovědí a z logů. Nová trasa `/api/answer` použije stejného klienta. Druhá veřejná služba není potřeba.

Poskytovatel chatu je Novita, OpenAI-kompatibilní `POST https://api.novita.ai/openai/v1/chat/completions`. RAG server na Novita chodí jen pro ingest embeddingy (`https://api.novita.ai/openai/v1/embeddings`, model `baai/bge-m3`, klíče `BGE_M3_NOVITA_API_KEY`, pak `NOVITA_API_KEY`, `OPENAI_TOKEN`, `OPENAI_API_KEY`). Search na RAG serveru je FastEmbed a tokeny Novita nespotřebuje. Doklad je [docs/embedding-provider.md](https://github.com/mkrejp/fastmcp-rag-timer/blob/main/docs/embedding-provider.md). Chat klíč se z těch embeddingových jmen nepřebírá.

Na službě `fastmcp-rag-timer-frontend` chat klíč dnes není. Je tam jen `MCP_API_TOKEN`. Marek doplní novou sealed proměnnou `NOVITA_LLM_API_KEY_GEMMA-3-27` jen na tuhle službu. Do gitu se nezapíše. Redaktor v [src/rag_frontend/redact.py](../src/rag_frontend/redact.py) dostane i tuhle hodnotu. Prohlížeč ji neuvidí.

| Proměnná | Výchozí |
| --- | --- |
| `LLM_BASE_URL` | `https://api.novita.ai/openai/v1` |
| `LLM_MODEL` | `google/gemma-3-27b-it` |
| `LLM_MODEL_FALLBACK` | `google/gemma-3-12b-it` |
| `NOVITA_LLM_API_KEY_GEMMA-3-27` | sealed, doplní Marek |

`google/gemma-3-27b-it` je serverless, bez reasoning tokenů, a na češtinu stačí. Ceník $0,119 / M vstup a $0,20 / M výstup. Rozpočet jednoho dotazu je asi 1500 tokenů vstupu a 400 výstupu, tedy zhruba $0,00026. Když má 27B výpadek, stejný request jde jednou na `google/gemma-3-12b-it` ($0,05 / $0,10). Druhý model se nezkouší, když první vrátí text. `max_tokens` je 400, `temperature` 0. To pořád je jedno chat volání na dotaz.

### jedno volání a když se nevolá

Na jeden dotaz smí odejít nejvýš jedno chat volání, a jen na syntézu. Rozšíření dotazu, parsování, deduplikace, šum a fulltext jsou regexy a slovník aliasů v kódu.

LLM se nevolá, když:

- po klasifikaci nezbyl žádný relevantní objekt,
- uživatel nebo env má režim bez LLM,
- denní limit uživatele nebo globální rozpočet je vyčerpaný.

V těch případech je odpověď složená šablonou z karet a šumu. Šablona řekne, kolik relevantních věcí server má (nula), a vypíše šum. Žádný odhad navíc.

### rozpočet

| Mez | Hodnota |
| --- | --- |
| Objekty v JSON pro model | nejvýš 5 |
| Snípet na objekt | 280 znaků, bez raw OCR (jen `image_id` a příznak, že OCR existuje) |
| Systémový prompt | pevný, pod 120 slov |
| Vstup do modelu | cíl ~1500 tokenů |
| `max_tokens` | 400 |
| Souběh searchů | 3 |
| Fulltextů na dotaz | nejvýš 8 ne-AKON dokumentů, které regex nebo search už vybral |
| Uživatel / den | 40 syntéz nebo 50 000 tokenů (prompt + completion), co nastane dřív |
| Globálně / den | 500 000 tokenů, pak tvrdý stop |

Počítadlo i cache odpovědí leží na novém volume `/data/answer` (frontend ho dnes nemá, spánek by paměť smazal a limit by přestal platit). Po stopu další dotaz nedostane chat, dostane šablonu a v QA panelu důvod. Novita `usage.prompt_tokens` a `usage.completion_tokens` se uloží k dotazu a ukážou v QA panelu. Do logu jde součet, ne prompt.

Přepínač `ANSWER_USE_LLM` (výchozí `true`). Stejný přepínač v session po přihlášení vypne syntézu pro další dotazy. Zapnout chat nad vyčerpaný rozpočet neumí.

### přihlášení

Celý web je za Google účtem. Schovat tlačítko nestačí. Middleware na Starlette ověří session před stránkou, statickými soubory, `/api/search`, `/api/catalog`, `/api/answer`, `/api/images` i `/api/documents`. Bez session HTML přesměruje na `/auth/login` (302). API vrátí 401 JSON. E-mail mimo allowlist dostane po návratu z Google 403 a session se nezaloží.

Knihovna: Authlib (`authlib.integrations.starlette_client`) plus `SessionMiddleware`, které FastAPI/Starlette už umí. Ruční OAuth z RAG serveru se sem nekopíruje. Rozsah je `openid email profile`.

Veřejné cesty jsou jen `/auth/login`, `/auth/callback`, `/auth/logout` a `GET /health` (prázdné `{"status":"ok"}` pro Railway). Dnešní health check na `/` by po zavedení loginu dostal 302 a deploy by spadl, proto se přepne na `/health`.

Google Cloud Console, typ klienta Web application:

| Pole | Hodnota |
| --- | --- |
| Authorized redirect URI (produkce) | `https://fastmcp-rag-timer-frontend-production.up.railway.app/auth/callback` |
| Redirect pro lokální běh | `http://127.0.0.1:8000/auth/callback` |
| Consent screen | External, scope email a profile. Dokud je aplikace ve Testing, musí být Marek mezi test users. |

Railway env na `fastmcp-rag-timer-frontend`, hodnoty jen v dashboardu:

| Proměnná | Smysl |
| --- | --- |
| `GOOGLE_CLIENT_ID` | z OAuth klienta |
| `GOOGLE_CLIENT_SECRET` | z OAuth klienta |
| `GOOGLE_ALLOWED_EMAILS` | čárkou oddělené adresy, porovnání bez velikosti písmen |
| `SESSION_SECRET` | náhodný řetězec, ne prázdný |
| `PUBLIC_BASE_URL` | `https://fastmcp-rag-timer-frontend-production.up.railway.app` |
| `NOVITA_LLM_API_KEY_GEMMA-3-27` | sealed, nová proměnná, doplní Marek |
| `LLM_BASE_URL` | `https://api.novita.ai/openai/v1` |
| `LLM_MODEL` | `google/gemma-3-27b-it` |
| `LLM_MODEL_FALLBACK` | `google/gemma-3-12b-it` |
| `ANSWER_USE_LLM` | `true` / `false` |

Cookie `SameSite=Lax`, `https_only` když `PUBLIC_BASE_URL` začíná na `https://`. Limit na uživatele je klíčovaný e-mailem ze session, ne IP.

## soubory

Měnit:

| Soubor | Změna |
| --- | --- |
| [src/rag_frontend/api/routes.py](../src/rag_frontend/api/routes.py) | `POST /api/answer`. Stávající `/api/search` a katalog nechat pro QA panel. |
| [src/rag_frontend/config.py](../src/rag_frontend/config.py) | `LLM_BASE_URL`, `LLM_MODEL`, `NOVITA_LLM_API_KEY_GEMMA-3-27`, přepínač a limity. Bez klíče syntéza spadne na šablonu. |
| [src/rag_frontend/web/index.html](../src/rag_frontend/web/index.html) | Jedna stránka: odpověď, karty, mapa, QA panel. |
| [src/rag_frontend/web/app.js](../src/rag_frontend/web/app.js) | Vykreslení odpovědi. Žádný MCP ani LLM klíč. |
| [src/rag_frontend/web/styles.css](../src/rag_frontend/web/styles.css) | Karty a odlišení centroidu. |
| [docs/ARCHITECTURE.md](ARCHITECTURE.md) | Až s implementací, ne v tomto PR. |

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

Prázdný dotaz: 400, bez volání ven. Chybí `MCP_API_TOKEN`: 503, bez hodnoty tokenu. Chybí `NOVITA_LLM_API_KEY_GEMMA-3-27`: šablona, chat se nevolá.

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
6. **Syntéza.** Nejvýš jedno volání `google/gemma-3-27b-it`. Bez relevantního objektu, bez klíče, při vypnutém přepínači nebo po vyčerpání rozpočtu se model nevolá a větu složí šablona.

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

Model, poskytovatel, jména `LLM_MODEL` / `LLM_BASE_URL` / `NOVITA_LLM_API_KEY_GEMMA-3-27`, jedno chat volání a přihlášení jsou rozhodnuté.

1. Kdo dodá slovník aliasů (Wrschowitz, sousední čtvrti) a pro které entity? Prázdný slovník historický název neuhodne.
2. Když dotaz nejmenuje kraj, podle čeho se homonymum v `obce.json` oddělí od té správné obce? Vršovice bez „Praha 10“ jsou v datech tři.
