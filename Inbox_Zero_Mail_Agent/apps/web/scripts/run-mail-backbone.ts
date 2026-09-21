/**
 * Runs the mail backbone as a standalone server next to the Google emulator:
 *
 *   pnpm emulate:google          # upstream emulator on :4002
 *   pnpm emulate:backbone        # this process, default :4005
 *
 * Point GOOGLE_BASE_URL at the backbone (not the emulator) so the app's
 * watch/stop calls are captured and notifications flow. Required env vars are
 * printed on startup failure; secrets are masked in the banner.
 */
import { startMailBackbone } from "./mail-backbone";

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    console.error(`Missing required env var: ${name}`);
    process.exit(1);
  }
  return value;
}

const port = Number(process.env.MAIL_BACKBONE_PORT ?? 4005);
const managedEmail = requireEnv("MANAGED_EMAIL");
const transportToken = requireEnv("TRANSPORT_TOKEN");
const webhookUrl = requireEnv("GOOGLE_WEBHOOK_URL");
const webhookVerificationToken = requireEnv("GOOGLE_PUBSUB_VERIFICATION_TOKEN");
const customerReceiveUrl = requireEnv("CUSTOMER_RECEIVE_URL");

const mask = (value: string) =>
  value.length <= 4 ? "***" : `${value.slice(0, 2)}***`;

async function main() {
  const backbone = await startMailBackbone({
    port,
    upstreamBaseUrl: process.env.EMULATOR_BASE_URL ?? "http://127.0.0.1:4002",
    managedEmail,
    transportToken,
    webhookUrl,
    webhookVerificationToken,
    customerReceiveUrl,
    stateDir: process.env.MAIL_BACKBONE_STATE_DIR ?? ".tmp/mail-backbone",
    pollIntervalMs: Number(process.env.MAIL_BACKBONE_POLL_INTERVAL_MS ?? 5000),
    emulatorAuthToken: process.env.EMULATOR_AUTH_TOKEN,
  });

  console.log(`Mail backbone listening on ${backbone.url}`);
  console.log("  MANAGED_EMAIL=", managedEmail);
  console.log("  TRANSPORT_TOKEN=", mask(transportToken));
  console.log("  WEBHOOK=", webhookUrl);
  console.log("  CUSTOMER_RECEIVE_URL=", customerReceiveUrl);

  for (const signal of ["SIGINT", "SIGTERM"] as const) {
    process.on(signal, async () => {
      await backbone.close();
      process.exit(0);
    });
  }
}

main().catch((error) => {
  console.error("Failed to start the mail backbone", error);
  process.exit(1);
});
