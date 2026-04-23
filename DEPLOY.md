# Deploy

## Recommended free option: Render

Render currently offers free web services for Python apps, with idle spin-down after 15 minutes and 750 free instance hours per month:
- https://render.com/docs/free

Steps:
1. Push this repo to GitHub.
2. In Render, choose `New -> Blueprint` or `New -> Web Service`.
3. Connect the GitHub repo.
4. If using Blueprint, Render will read `render.yaml`.
5. Add environment variables:
   - `TELEGRAM_TOKEN`
   - `CHANNEL_ID`
   - `GEMINI_TOKEN` (optional; bot works without it)
   - `CHECK_INTERVAL=1800`
   - `MAX_PER_FEED=2`
6. Deploy.

## Free backup option: Koyeb

Koyeb currently offers one free web instance with 512MB RAM, 0.1 vCPU, and scale-to-zero after 1 hour without traffic:
- https://www.koyeb.com/docs/reference/instances

Steps:
1. Push this repo to GitHub.
2. In Koyeb, create a new `Web Service` from the GitHub repo.
3. Use the included `Dockerfile`.
4. Set the same environment variables as above.
5. Expose port `7860` if Koyeb asks for it.

## Notes

- Hugging Face Space could parse feeds, but outbound requests to Telegram were timing out.
- This app already exposes a health endpoint on `/` and is ready for generic Docker deployment.
- If `GEMINI_TOKEN` has no quota, the bot falls back to a plain formatted RSS summary.
