/**
 * Env prelude for the oracle scripts: must be imported before anything that
 * pulls in @/env (i.e. before @/utils/prisma). apps/web/.env deliberately
 * stores POSTGRES_* / UPSTASH_REDIS_TOKEN instead of connection strings
 * (mail-loop.sh passes those to the app explicitly), so standalone scripts
 * rebuild them here.
 */
import "dotenv/config";

Object.assign(process.env, { NODE_ENV: process.env.NODE_ENV || "development" });

if (!process.env.DATABASE_URL && process.env.POSTGRES_PASSWORD) {
  const user = process.env.POSTGRES_USER || "postgres";
  const port = process.env.POSTGRES_PORT || "5433";
  const db = process.env.POSTGRES_DB || "inboxzero";
  process.env.DATABASE_URL = `postgresql://${user}:${process.env.POSTGRES_PASSWORD}@127.0.0.1:${port}/${db}?schema=public`;
  process.env.DIRECT_URL ||= process.env.DATABASE_URL;
}

if (!process.env.UPSTASH_REDIS_URL && process.env.UPSTASH_REDIS_TOKEN) {
  process.env.UPSTASH_REDIS_URL = "http://127.0.0.1:8079";
}
