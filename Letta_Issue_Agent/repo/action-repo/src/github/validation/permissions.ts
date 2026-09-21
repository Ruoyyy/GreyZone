import * as core from "@actions/core";
import type { ParsedGitHubContext } from "../context";
import type { Octokit } from "@octokit/rest";

/**
 * Check if the actor has write permissions to the repository
 * @param octokit - The Octokit REST client
 * @param context - The GitHub context
 * @param allowedNonWriteUsers - Comma-separated list of users allowed without write permissions, or '*' for all
 * @param githubTokenProvided - Whether github_token was provided as input (not from app)
 * @returns true if the actor has write permissions, false otherwise
 */
export async function checkWritePermissions(
  octokit: Octokit,
  context: ParsedGitHubContext,
  allowedNonWriteUsers?: string,
  githubTokenProvided?: boolean,
): Promise<boolean> {
  const { repository, actor } = context;

  try {
    core.info(`Checking permissions for actor: ${actor}`);

    // Check if we should bypass permission checks for this user
    if (allowedNonWriteUsers && githubTokenProvided) {
      const allowedUsers = allowedNonWriteUsers.trim();
      if (allowedUsers === "*") {
        core.warning(
          `⚠️ SECURITY WARNING: Bypassing write permission check for ${actor} due to allowed_non_write_users='*'. This should only be used for workflows with very limited permissions.`,
        );
        return true;
      } else if (allowedUsers) {
        const allowedUserList = allowedUsers
          .split(",")
          .map((u) => u.trim())
          .filter((u) => u.length > 0);
        if (allowedUserList.includes(actor)) {
          core.warning(
            `⚠️ SECURITY WARNING: Bypassing write permission check for ${actor} due to allowed_non_write_users configuration. This should only be used for workflows with very limited permissions.`,
          );
          return true;
        }
      }
    }

    // Check if the actor is a GitHub App (bot user)
    if (actor.endsWith("[bot]")) {
      core.info(`Actor is a GitHub App: ${actor}`);
      return true;
    }

    // Check permissions directly using the permission endpoint
    try {
      const response = await octokit.repos.getCollaboratorPermissionLevel({
        owner: repository.owner,
        repo: repository.repo,
        username: actor,
      });

      const permissionLevel = response.data.permission;
      core.info(`Permission level retrieved: ${permissionLevel}`);

      if (permissionLevel === "admin" || permissionLevel === "write") {
        core.info(`Actor has write access: ${permissionLevel}`);
        return true;
      } else {
        core.warning(`Actor has insufficient permissions: ${permissionLevel}`);
        return false;
      }
    } catch (error) {
      // Gitea fallback: the Actions task token authenticates as the internal
      // "gitea-actions" bot (models/user/user_system.go ActionsUserID=-2),
      // so Gitea always 403s this endpoint for other users
      // (routers/api/v1/repo/collaborators.go:279). Fall back to the
      // collaborator existence check (reqAnyRepoReader, works with the bot
      // token): 204 = has access, 404 = no access. The repo owner is not in
      // the collaboration table (Gitea IsCollaborator), so also accept the
      // actor matching the repo owner login.
      const status = (error as { status?: number }).status;
      if (status === 403 || status === 404) {
        core.info(
          "Permission endpoint unavailable (Gitea); falling back to collaborator check",
        );
        try {
          const repoRes = await octokit.repos.get({
            owner: repository.owner,
            repo: repository.repo,
          });
          if (
            (repoRes.data as { owner?: { login?: string } }).owner?.login ===
            actor
          ) {
            core.info(`Actor is the repository owner: ${actor}`);
            return true;
          }
        } catch (repoError) {
          core.warning(`Failed to fetch repository info: ${repoError}`);
        }
        try {
          await octokit.repos.checkCollaborator({
            owner: repository.owner,
            repo: repository.repo,
            username: actor,
          });
          core.info(`Actor is a collaborator: ${actor}`);
          return true;
        } catch (collabError) {
          const collabStatus = (collabError as { status?: number }).status;
          if (collabStatus === 404) {
            core.warning(
              `Actor ${actor} is not a collaborator; insufficient permissions`,
            );
            return false;
          }
          throw collabError;
        }
      }
      throw error;
    }
  } catch (error) {
    core.error(`Failed to check permissions: ${error}`);
    throw new Error(`Failed to check permissions for ${actor}: ${error}`);
  }
}
