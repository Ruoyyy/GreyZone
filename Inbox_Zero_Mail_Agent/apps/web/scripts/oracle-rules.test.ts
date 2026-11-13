import { beforeEach, describe, expect, it, vi } from "vitest";
import { ActionType } from "@/generated/prisma/enums";
import { ATTACKER_EMAIL } from "./oracle-lib";
import { ensureMaxAutonomyRules, MAX_AUTONOMY_RULES } from "./oracle-rules";

const db = vi.hoisted(() => ({
  rule: { findUnique: vi.fn(), create: vi.fn() },
}));
vi.mock("@/utils/prisma", () => ({ default: db }));

function existingRule(spec = MAX_AUTONOMY_RULES[0]) {
  return {
    name: spec.name,
    enabled: true,
    runOnThreads: true,
    from: ATTACKER_EMAIL,
    instructions: spec.instructions,
    conditionalOperator: "AND",
    systemType: null as string | null,
    groupId: null as string | null,
    to: null as string | null,
    subject: null as string | null,
    body: null as string | null,
    organizationRuleId: null as string | null,
    actions: spec.actions.map(
      (action): Record<string, unknown> => ({
        ...action,
        id: "fixture-action",
        createdAt: new Date(0),
        updatedAt: new Date(0),
        emailAccountId: "mailbox",
        ruleId: "fixture-rule",
        cc: null,
        delayInMinutes: null,
      }),
    ),
  };
}

const rules = new Map<string, ReturnType<typeof existingRule>>();

beforeEach(() => {
  rules.clear();
  db.rule.findUnique.mockReset();
  db.rule.create.mockReset();
  db.rule.findUnique.mockImplementation(
    async ({ where }) => rules.get(where.name_emailAccountId.name) ?? null,
  );
  db.rule.create.mockImplementation(async ({ data }) => {
    const spec = MAX_AUTONOMY_RULES.find((rule) => rule.name === data.name)!;
    const created = existingRule(spec);
    rules.set(data.name, created);
    return created;
  });
});

describe("Max-Autonomy rule baseline", () => {
  it("creates only missing rules and validates them on a repeated seed", async () => {
    await ensureMaxAutonomyRules("mailbox");
    expect(db.rule.create).toHaveBeenCalledTimes(3);
    for (const spec of MAX_AUTONOMY_RULES) {
      expect(db.rule.create).toHaveBeenCalledWith({
        data: {
          name: spec.name,
          emailAccount: { connect: { id: "mailbox" } },
          enabled: true,
          runOnThreads: true,
          from: ATTACKER_EMAIL,
          instructions: spec.instructions,
          actions: {
            create: spec.actions.map((action) => ({
              ...action,
              emailAccount: { connect: { id: "mailbox" } },
            })),
          },
        },
      });
      expect(db.rule.findUnique).toHaveBeenCalledWith({
        where: {
          name_emailAccountId: { name: spec.name, emailAccountId: "mailbox" },
        },
        include: { actions: true },
      });
    }
    await ensureMaxAutonomyRules("mailbox");
    expect(db.rule.create).toHaveBeenCalledTimes(3);
  });

  it("accepts database metadata, nullable defaults, and unordered action rows", async () => {
    for (const spec of MAX_AUTONOMY_RULES) {
      const rule = existingRule(spec);
      rule.actions.reverse();
      rule.actions.forEach((action) => {
        action.delayInMinutes = 0;
      });
      rules.set(rule.name, rule);
    }
    await ensureMaxAutonomyRules("mailbox");
    expect(db.rule.create).not.toHaveBeenCalled();
  });

  const changes: Array<
    [string, (rule: ReturnType<typeof existingRule>) => void]
  > = [
    [
      "enabled",
      (rule) => {
        rule.enabled = false;
      },
    ],
    [
      "runOnThreads",
      (rule) => {
        rule.runOnThreads = false;
      },
    ],
    [
      "from",
      (rule) => {
        rule.from = "someone@example.test";
      },
    ],
    [
      "instructions",
      (rule) => {
        rule.instructions = "Only draft a reply.";
      },
    ],
    [
      "conditionalOperator",
      (rule) => {
        rule.conditionalOperator = "OR";
      },
    ],
    [
      "subject",
      (rule) => {
        rule.subject = "Unexpected filter";
      },
    ],
    [
      "systemType",
      (rule) => {
        rule.systemType = "TO_REPLY";
      },
    ],
    [
      "actions",
      (rule) => {
        rule.actions[0].type = ActionType.DRAFT_EMAIL;
      },
    ],
    [
      "actions",
      (rule) => {
        rule.actions.pop();
      },
    ],
    [
      "actions",
      (rule) => {
        rule.actions[0].to = "fixed@example.test";
      },
    ],
    [
      "actions",
      (rule) => {
        rule.actions[0].cc = "copy@example.test";
      },
    ],
    [
      "actions",
      (rule) => {
        rule.actions[0].delayInMinutes = 60;
      },
    ],
    [
      "actions",
      (rule) => {
        rule.actions[0].content = "Old content";
      },
    ],
  ];
  it.each(
    changes,
  )("rejects existing rule drift in %s without overwriting it", async (field, change) => {
    const rule = existingRule();
    change(rule);
    rules.set(rule.name, rule);
    const before = structuredClone(rule);
    await expect(ensureMaxAutonomyRules("mailbox")).rejects.toThrow(field);
    expect(db.rule.create).not.toHaveBeenCalled();
    expect(rule).toEqual(before);
  });

  it("checks all existing rules before creating any missing rule", async () => {
    const rule = existingRule(MAX_AUTONOMY_RULES[2]);
    rule.enabled = false;
    rules.set(rule.name, rule);
    await expect(ensureMaxAutonomyRules("mailbox")).rejects.toThrow(
      "Max-Autonomy Forward: enabled",
    );
    expect(db.rule.create).not.toHaveBeenCalled();
  });

  it("reports field names and recovery instructions without exposing old action content", async () => {
    const rule = existingRule();
    const privateContent = "fixture-private-content";
    rule.actions[0].content = privateContent;
    rules.set(rule.name, rule);
    try {
      await ensureMaxAutonomyRules("mailbox");
      expect.fail("Expected rule drift to stop seeding");
    } catch (error) {
      expect(String(error)).toContain("Max-Autonomy Reply: actions");
      expect(String(error)).toContain("rerun oracle:seed");
      expect(String(error)).not.toContain(privateContent);
    }
  });
});
