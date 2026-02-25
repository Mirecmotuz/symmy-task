# ERP–Eshop Synchronizačný most

Robustný synchronizačný bridge medzi ERP systémom a e-shopom implementovaný ako Django + Celery aplikácia.

## Architektúra

```
erp_data.json  ──▶  transformer.py  ──▶  delta sync (DB)  ──▶  eshop_client.py  ──▶  API
                     - sum stocks          ProductSyncState      - rate limit 5 req/s
                     - +21 % DPH           SHA-256 hash          - retry on 429
                     - default color
```

### Komponenty

| Súbor | Zodpovednosť |
|---|---|
| `integrator/transformer.py` | Načítanie ERP dát, transformácia, výpočet hashu |
| `integrator/eshop_client.py` | HTTP klient s rate limitingom a retry logikou |
| `integrator/models.py` | `ProductSyncState` – sledovanie posledného sync stavu |
| `integrator/tasks.py` | Celery task orchestrujúci celý flow |

### Delta Sync

Každý produkt sa po transformácii ohasuje (SHA-256). Hash sa uloží do `ProductSyncState`. Pri ďalšom spustení sa produkty s rovnakým hashom preskočia – API sa volá len pre zmenené alebo nové produkty.

### Rate Limiting a Retry

- Klient udržuje max **5 requestov za sekundu** pomocou token-bucket algoritmu.
- Pri HTTP **429** sa použije hodnota z `Retry-After` headera; ak header chýba, použije sa exponenciálny backoff (1 s → 2 s → 4 s).
- Po **3 neúspešných pokusoch** sa vyhodí výnimka.

### Ošetrené edge-cases v ERP dátach

| Problém | Riešenie |
|---|---|
| Záporná cena | Produkt preskočený + `WARNING` log |
| `null` cena | Produkt preskočený + `WARNING` log |
| Duplicitný SKU | Ponechaný prvý výskyt, zvyšné preskočené |
| Stock `"N/A"` (string) | Považovaný za 0 |
| Chýbajúca / `null` farba | Default hodnota `"N/A"` |

---

## Spustenie krok za krokom

### Predpoklady

Pred spustením musíš mať nainštalované:

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) – obsahuje Docker aj Docker Compose

Nič iné nie je potrebné – Python, PostgreSQL ani Redis inštalovať netreba. Všetko beží v kontajneroch.

---

### Krok 1 – Spusti Docker Desktop

Otvor aplikáciu Docker Desktop a počkaj, kým sa plne naštartuje. Spoznáš to podľa toho, že ikona v systémovej lište prestane animovať. Bez spusteného Dockera žiadny z ďalších príkazov nebude fungovať.

---

### Krok 2 – Otvor terminál v priečinku projektu

Prejdi do koreňového priečinka projektu (tam kde sa nachádza súbor `docker-compose.yml`):

```bash
cd cesta/k/symmy-task/symmy-task
```

---

### Krok 3 – Zostav a spusti všetky kontajnery

```bash
docker compose up --build
```

Tento príkaz stiahne potrebné Docker obrazy, nainštaluje závislosti a spustí 4 kontajnery:

| Kontajner | Popis | Port |
|---|---|---|
| `db` | PostgreSQL 15 – databáza | 5433 |
| `redis` | Redis 7 – broker správ pre Celery | interný |
| `web` | Django development server | http://localhost:8000 |
| `worker` | Celery worker – spracováva async tasky | – |

> **Prvé spustenie** trvá dlhšie (sťahovanie obrazov, inštalácia balíčkov). Každé ďalšie spustenie je výrazne rýchlejšie.

Terminál nechaj bežať – logy všetkých kontajnerov sa zobrazujú tu.

---

### Krok 4 – Spusti migrácie databázy

V **novom termináli** (pôvodný nechaj otvorený) spusti:

```bash
docker compose run --rm web python manage.py migrate
```

Toto vytvorí potrebné tabuľky v databáze. Stačí urobiť raz (alebo po každej zmene modelov).

---

### Krok 5 – Spusti synchronizáciu

**Možnosť A – priamo (synchrónne, jednoduchšie na testovanie):**

```bash
docker compose run --rm web python manage.py shell -c "from integrator.tasks import sync_products_task; sync_products_task()"
```

Task prebehne ihneď v termináli a vypíše výsledok (koľko produktov bolo odoslaných, preskočených, chybových).

**Možnosť B – cez Celery (asynchrónne, ako v produkcii):**

```bash
docker compose run --rm web python manage.py shell -c "from integrator.tasks import sync_products_task; sync_products_task.delay()"
```

Task sa zaradí do fronty a spracuje ho `worker` kontajner. Výsledok uvidíš v logoch pôvodného terminálu.

---

### Zastavenie aplikácie

Ak chceš zastaviť všetky kontajnery, stlač `Ctrl+C` v termináli kde beží `docker compose up`, alebo v novom termináli:

```bash
docker compose down
```

Ak chceš zmazať aj databázové dáta a začať od nuly:

```bash
docker compose down -v
```

---

## Testy

Testy bežia cez `pytest` s reálnou testovacou DB (`pytest-django`) a mockovaným HTTP API (`responses`). Na spustenie testov **musia bežať kontajnery** (aspoň `db`).

Spusti testy:

```bash
docker compose run --rm web pytest
```

So podrobným výpisom každého testu:

```bash
docker compose run --rm web pytest -v
```

### Štruktúra testov

| Súbor | Čo testuje |
|---|---|
| `tests/test_transformer.py` | Transformačná logika, edge-cases, deduplication, hashování |
| `tests/test_eshop_client.py` | POST/PATCH volania, API key header, retry pri 429, rate limit, thread safety |
| `tests/test_tasks.py` | Celý sync flow, delta sync, perzistencia hashov, správanie pri chybách |

---

## Premenné prostredia

Hodnoty sú prednastavené v `docker-compose.yml`. Pre vlastné nasadenie ich môžeš prepísať:

| Premenná | Default | Popis |
|---|---|---|
| `DATABASE_URL` | (z docker-compose) | PostgreSQL pripojenie |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Redis URL pre Celery |
| `ESHOP_API_BASE_URL` | `https://api.fake-eshop.cz/v1` | Base URL eshop API |
| `ESHOP_API_KEY` | `symma-secret-token` | API kľúč |
