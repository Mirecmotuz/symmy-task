Kontext:
Tvým úkolem je vytvořit robustní synchronizační můstek mezi ERP systémem a e-shopem. Klíčem je stabilita, efektivita a schopnost poradit si s limity externích API.
Technické požadavky
Logika: Implementuj synchronizační proces v aplikaci integrator (základ úkolu v integrator/tasks.py)
Vstup: Data ze souboru erp_data.json (v rootu projektu). Tento soubor simuluje reálný ERP endpoint – načti jej přímo z disku.
Transformace: Skladové zásoby sečti, k ceně připočti 21 % DPH, ošetři chybějící barvu (default "N/A")
Delta Sync: Na API e-shopu posílej pouze produkty, které se od poslední synchronizace změnily (použij hashování nebo timestamp v DB)
Rate Limiting: E-shop API má limit 5 req/s. Při statusu 429 musí systém počkat a pokus opakovat (retry)
Asynchronita: Celý proces musí běžet jako Celery task
Simulovaná Dokumentace API
Cílové API je fiktivní. Pro otestování komunikace a rate-limitingu použij mockování (např. responses nebo unittest.mock).
Base URL: https://api.fake-eshop.cz/v1
Auth: Header X-Api-Key: symma-secret-token
Metody: POST /products/ pro nové, PATCH /products/{sku}/ pro existující
Odevzdání
Odkaz na GitHub/GitLab.
README s popisem architektury a návodem (očekáváme docker-compose up).
Testy pokrývající transformační logiku a mockování API.
