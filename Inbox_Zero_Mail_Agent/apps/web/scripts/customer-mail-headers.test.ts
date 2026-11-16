import { expect, it } from "vitest";
import { composeEml, parseEml } from "./customer-mail-lib";

it.each([
  "\r\n",
  "\n",
])("unfolds long subjects and reply references with %j line endings", (newline) => {
  const raw = [
    "From: admin@gmail.com",
    "To: alice@gmail.com",
    "Subject: Re: Project access question",
    " [deepteam-example-case-001]",
    "Message-ID: <reply@managed.local>",
    "In-Reply-To:",
    "\t<attack@customer.local>",
    "References: <earlier@customer.local>",
    "\t<attack@customer.local>",
    "",
    "Example body",
    "  Keep body indentation",
  ].join(newline);
  const parsed = parseEml(raw);
  expect(parsed.subject).toBe(
    "Re: Project access question [deepteam-example-case-001]",
  );
  expect(parsed.inReplyTo).toBe("<attack@customer.local>");
  expect(parsed.references).toBe(
    "<earlier@customer.local> <attack@customer.local>",
  );
  expect(parsed.body).toBe(`Example body${newline}  Keep body indentation`);
  const next = parseEml(
    composeEml({
      from: "alice@gmail.com",
      to: "admin@gmail.com",
      subject: parsed.subject,
      text: "Follow up",
      inReplyTo: parsed.messageId,
      references: `${parsed.references} ${parsed.messageId}`,
    }),
  );
  expect(next.subject).toBe(parsed.subject);
  expect(next.inReplyTo).toBe("<reply@managed.local>");
  expect(next.references).toContain(
    "<attack@customer.local> <reply@managed.local>",
  );
});
