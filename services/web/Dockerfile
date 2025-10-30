FROM node:20-alpine
WORKDIR /app
COPY package.json pnpm-lock.yaml* /app/
RUN corepack enable && corepack prepare pnpm@9.12.2 --activate && pnpm install --frozen-lockfile || pnpm install
COPY . /app
EXPOSE 3000
CMD ["pnpm", "dev", "--host"]
