# PartsFlip Backend

Real-time price scraper for UK auto parts suppliers.

## What it does
- Scrapes live prices from GSF Car Parts, Euro Car Parts, Halfords, Autodoc
- Automatically finds and applies discount codes from each supplier's own website
- Gets real eBay UK lowest prices
- Returns everything to the PartsFlip frontend

## Deploy to Railway (free)

1. Go to railway.app and sign up (free)
2. Click "New Project" → "Deploy from GitHub repo"
3. Push this folder to a GitHub repo first:
   ```
   git init
   git add .
   git commit -m "PartsFlip backend"
   gh repo create partsflip-backend --public --push
   ```
4. Connect the repo in Railway
5. Railway auto-detects Python and deploys
6. Copy your Railway URL (e.g. https://partsflip-backend.up.railway.app)
7. Paste it into the PartsFlip app backend URL field

## API Endpoints

GET /health                          - Health check
GET /search?q=transit+air+filter     - Search all suppliers
GET /ebay-price?q=bosch+air+filter   - Get eBay lowest price
GET /scan?vehicle=...&parts=...      - Batch scan parts
GET /codes?supplier=gsf              - Get discount codes

## Local testing

```
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000/health
