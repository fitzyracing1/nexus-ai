# Chromium + all its OS deps come baked into the Playwright image, so the
# browser "just works" on any host (Render, Railway, Fly.io, a plain VPS…).
FROM mcr.microsoft.com/playwright:v1.56.1-jammy

WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev
COPY . .

ENV PORT=8080
EXPOSE 8080

# Pin your own secret in the platform's env: CB_TOKEN=<something-long>
# (If unset, the server generates one and prints it to the logs on boot.)
CMD ["node", "server.js"]
