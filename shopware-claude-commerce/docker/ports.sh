#!/usr/bin/env bash
# Shared host ports and URLs. Read dotenv as data, never as shell commands.
load_port_config() {
  local values key value
  values="$(node --input-type=module - "$ROOT/.env" <<'JS'
import fs from 'node:fs';
import { parseEnv } from 'node:util';
const file = fs.existsSync(process.argv[2]) ? parseEnv(fs.readFileSync(process.argv[2], 'utf8')) : {};
const get = (key, fallback) => process.env[key] || file[key] || fallback;
const ports = { SHOPWARE_HTTP_PORT: '8080', SHOPWARE_MYSQL_PORT: '3306', STOREFRONT_API_PORT: '8004', MERCHANT_API_PORT: '8005' };
for (const [key, fallback] of Object.entries(ports)) {
  ports[key] = get(key, fallback);
  if (!/^\d+$/.test(ports[key]) || Number(ports[key]) < 1 || Number(ports[key]) > 65535) throw new Error(`Invalid port: ${key}`);
}
const shop = get('SHOPWARE_PUBLIC_URL', get('SHOPWARE_URL', `http://localhost:${ports.SHOPWARE_HTTP_PORT}`));
const storefront = get('STOREFRONT_API_PUBLIC_URL', `http://localhost:${ports.STOREFRONT_API_PORT}`);
const values = { ...ports, SHOPWARE_PUBLIC_URL: shop, SHOPWARE_URL: get('SHOPWARE_URL', shop),
  SHOPWARE_ADMIN_URL: get('SHOPWARE_ADMIN_URL', shop), STOREFRONT_API_PUBLIC_URL: storefront,
  STOREFRONT_API_URL: get('STOREFRONT_API_URL', storefront),
  MERCHANT_API_URL: get('MERCHANT_API_URL', `http://localhost:${ports.MERCHANT_API_PORT}`) };
values.SELLER_MCP_URL = get('SELLER_MCP_URL', `${values.STOREFRONT_API_URL.replace(/\/$/, '')}/seller/mcp/`);
for (const [key, value] of Object.entries(values)) {
  if (/[\r\n]/.test(value)) throw new Error(`Invalid config: ${key}`);
  if (key.endsWith('_URL') && !/^https?:\/\//.test(value)) throw new Error(`Expected HTTP URL: ${key}`);
  console.log(`${key}=${value}`);
}
JS
  )" || return 1
  while IFS='=' read -r key value; do export "$key=$value"; done <<<"$values"
}
