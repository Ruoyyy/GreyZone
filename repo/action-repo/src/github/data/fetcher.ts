import { execFileSync } from "child_process";
import type { Octokits } from "../api/client";
import {
  isIssueCommentEvent,
  isPullRequestReviewEvent,
  isPullRequestReviewCommentEvent,
  type ParsedGitHubContext,
} from "../context";
import type {
  GitHubComment,
  GitHubFile,
  GitHubIssue,
  GitHubPullRequest,
  GitHubReview,
  GitHubReviewComment,
} from "../types";
import type { CommentWithImages } from "../utils/image-downloader";
import { downloadCommentImages } from "../utils/image-downloader";

/**
 * Extracts the trigger timestamp from the GitHub webhook payload.
 * This timestamp represents when the triggering comment/review/event was created.
 *
 * @param context - Parsed GitHub context from webhook
 * @returns ISO timestamp string or undefined if not available
 */
export function extractTriggerTimestamp(
  context: ParsedGitHubContext,
): string | undefined {
  if (isIssueCommentEvent(context)) {
    return context.payload.comment.created_at || undefined;
  } else if (isPullRequestReviewEvent(context)) {
    return context.payload.review.submitted_at || undefined;
  } else if (isPullRequestReviewCommentEvent(context)) {
    return context.payload.comment.created_at || undefined;
  }

  return undefined;
}

/**
 * Filters comments to only include those that existed in their final state before the trigger time.
 * This prevents malicious actors from editing comments after the trigger to inject harmful content.
 *
 * @param comments - Array of GitHub comments to filter
 * @param triggerTime - ISO timestamp of when the trigger comment was created
 * @returns Filtered array of comments that were created and last edited before trigger time
 */
export function filterCommentsToTriggerTime<
  T extends { createdAt: string; updatedAt?: string; lastEditedAt?: string },
>(comments: T[], triggerTime: string | undefined): T[] {
  if (!triggerTime) return comments;

  const triggerTimestamp = new Date(triggerTime).getTime();

  return comments.filter((comment) => {
    // Comment must have been created before trigger (not at or after)
    const createdTimestamp = new Date(comment.createdAt).getTime();
    if (createdTimestamp >= triggerTimestamp) {
      return false;
    }

    // If comment has been edited, the most recent edit must have occurred before trigger
    // Use lastEditedAt if available, otherwise fall back to updatedAt
    const lastEditTime = comment.lastEditedAt || comment.updatedAt;
    if (lastEditTime) {
      const lastEditTimestamp = new Date(lastEditTime).getTime();
      if (lastEditTimestamp >= triggerTimestamp) {
        return false;
      }
    }

    return true;
  });
}

/**
 * Filters reviews to only include those that existed in their final state before the trigger time.
 * Similar to filterCommentsToTriggerTime but for GitHubReview objects which use submittedAt instead of createdAt.
 */
export function filterReviewsToTriggerTime<
  T extends { submittedAt: string; updatedAt?: string; lastEditedAt?: string },
>(reviews: T[], triggerTime: string | undefined): T[] {
  if (!triggerTime) return reviews;

  const triggerTimestamp = new Date(triggerTime).getTime();

  return reviews.filter((review) => {
    // Review must have been submitted before trigger (not at or after)
    const submittedTimestamp = new Date(review.submittedAt).getTime();
    if (submittedTimestamp >= triggerTimestamp) {
      return false;
    }

    // If review has been edited, the most recent edit must have occurred before trigger
    const lastEditTime = review.lastEditedAt || review.updatedAt;
    if (lastEditTime) {
      const lastEditTimestamp = new Date(lastEditTime).getTime();
      if (lastEditTimestamp >= triggerTimestamp) {
        return false;
      }
    }

    return true;
  });
}

/**
 * Checks if the issue/PR body was edited after the trigger time.
 * This prevents a race condition where an attacker could edit the issue/PR body
 * between when an authorized user triggered Letta and when Letta processes the request.
 *
 * @param contextData - The PR or issue data containing body and edit timestamps
 * @param triggerTime - ISO timestamp of when the trigger event occurred
 * @returns true if the body is safe to use, false if it was edited after trigger
 */
export function isBodySafeToUse(
  contextData: { createdAt: string; updatedAt?: string; lastEditedAt?: string },
  triggerTime: string | undefined,
): boolean {
  // If no trigger time is available, we can't validate - allow the body
  // This maintains backwards compatibility for triggers that don't have timestamps
  if (!triggerTime) return true;

  const triggerTimestamp = new Date(triggerTime).getTime();

  // Check if the body was edited after the trigger
  // Use lastEditedAt if available (more accurate for body edits), otherwise fall back to updatedAt
  const lastEditTime = contextData.lastEditedAt || contextData.updatedAt;
  if (lastEditTime) {
    const lastEditTimestamp = new Date(lastEditTime).getTime();
    if (lastEditTimestamp >= triggerTimestamp) {
      return false;
    }
  }

  return true;
}

type FetchDataParams = {
  octokits: Octokits;
  repository: string;
  prNumber: string;
  isPR: boolean;
  triggerUsername?: string;
  triggerTime?: string;
};

export type GitHubFileWithSHA = GitHubFile & {
  sha: string;
};

export type FetchDataResult = {
  contextData: GitHubPullRequest | GitHubIssue;
  comments: GitHubComment[];
  changedFiles: GitHubFile[];
  changedFilesWithSHA: GitHubFileWithSHA[];
  reviewData: { nodes: GitHubReview[] } | null;
  imageUrlMap: Map<string, string>;
  triggerDisplayName?: string | null;
};

export async function fetchGitHubData({
  octokits,
  repository,
  prNumber,
  isPR,
  triggerUsername,
  triggerTime,
}: FetchDataParams): Promise<FetchDataResult> {
  const [owner, repo] = repository.split("/");
  if (!owner || !repo) {
    throw new Error("Invalid repository format. Expected 'owner/repo'.");
  }

  let contextData: GitHubPullRequest | GitHubIssue | null = null;
  let comments: GitHubComment[] = [];
  let changedFiles: GitHubFile[] = [];
  let reviewData: { nodes: GitHubReview[] } | null = null;

  try {
    if (isPR) {
      // Fetch PR via REST (Gitea has no GraphQL): PR meta + commits + files + reviews + comments
      const prRes = await octokits.rest.pulls.get({
        owner,
        repo,
        pull_number: parseInt(prNumber),
      });
      const pr = prRes.data;

      const [commitsRes, filesRes, reviewsRes] = await Promise.all([
        octokits.rest.pulls.listCommits({
          owner,
          repo,
          pull_number: parseInt(prNumber),
        }),
        octokits.rest.pulls.listFiles({
          owner,
          repo,
          pull_number: parseInt(prNumber),
        }),
        octokits.rest.pulls.listReviews({
          owner,
          repo,
          pull_number: parseInt(prNumber),
        }),
      ]);
      // Gitea compat: GET /pulls/{n}/comments (global review-comment list)
      // doesn't exist in Gitea; fetch per review instead (same data, grouped).
      const reviewCommentsRes = await Promise.all(
        reviewsRes.data.map((r) =>
          octokits.rest.pulls.listReviewComments({
            owner,
            repo,
            pull_number: parseInt(prNumber),
            review_id: r.id as number,
          }),
        ),
      );

      // Group review comments by review_id for the nested reviews[].comments shape
      const reviewCommentsByReview = new Map<
        number,
        GitHubReviewComment[]
      >();
      for (const [i, res] of reviewCommentsRes.entries()) {
        const review = reviewsRes.data[i];
        if (!review) continue;
        const rid = review.id as number;
        const list = reviewCommentsByReview.get(rid) || [];
        for (const rc of res.data) {
          list.push({
            id: String(rc.id),
            databaseId: String(rc.id),
            body: rc.body || "",
            author: { login: (rc.user as any)?.login || "" },
            createdAt: rc.created_at || "",
            updatedAt: rc.updated_at,
            path: rc.path || "",
            line: rc.line ?? rc.original_line ?? null,
          });
        }
        reviewCommentsByReview.set(rid, list);
      }

      const reviews: GitHubReview[] = reviewsRes.data.map((r) => ({
        id: String(r.id),
        databaseId: String(r.id),
        author: { login: (r.user as any)?.login || "" },
        body: r.body || "",
        state: (r.state || "").toUpperCase(),
        submittedAt: r.submitted_at || "",
        updatedAt: undefined,
        comments: { nodes: reviewCommentsByReview.get(r.id as any) || [] },
      }));

      const commits = {
        totalCount: commitsRes.data.length,
        nodes: commitsRes.data.map((c) => ({
          commit: {
            oid: c.sha || "",
            message: c.commit?.message || "",
            author: {
              name: c.commit?.author?.name || "",
              email: c.commit?.author?.email || "",
            },
          },
        })),
      };

      const files = {
        nodes: (filesRes.data as any[]).map((f) => ({
          path: f.filename || "",
          additions: f.additions || 0,
          deletions: f.deletions || 0,
          // GitHub GraphQL changeType: ADDED/MODIFIED/DELETED/RENAMED
          // Gitea REST status: added/modified/removed/renamed
          changeType: (f.status || "modified").toUpperCase(),
        })),
      };

      const prCommentsRes = await octokits.rest.issues.listComments({
        owner,
        repo,
        issue_number: parseInt(prNumber),
      });
      const prComments = {
        nodes: prCommentsRes.data.map((c) => ({
          id: String(c.id),
          databaseId: String(c.id),
          body: c.body || "",
          author: { login: (c.user as any)?.login || "" },
          createdAt: c.created_at || "",
          updatedAt: c.updated_at,
        })),
      };

      contextData = {
        title: pr.title || "",
        body: pr.body || "",
        author: { login: (pr.user as any)?.login || "" },
        baseRefName: (pr.base as any)?.ref || "",
        headRefName: (pr.head as any)?.ref || "",
        headRefOid: (pr.head as any)?.sha || "",
        createdAt: pr.created_at || "",
        updatedAt: pr.updated_at,
        additions: (pr as any).additions || 0,
        deletions: (pr as any).deletions || 0,
        state: (pr.state || "").toUpperCase(),
        commits,
        files,
        comments: prComments,
        reviews: { nodes: reviews },
      } as GitHubPullRequest;

      changedFiles = files.nodes;
      comments = filterCommentsToTriggerTime(prComments.nodes, triggerTime);
      reviewData = { nodes: reviews };

      console.log(`Successfully fetched PR #${prNumber} data`);
    } else {
      // Fetch issue via REST
      const issueRes = await octokits.rest.issues.get({
        owner,
        repo,
        issue_number: parseInt(prNumber),
      });
      const issue = issueRes.data;

      const commentsRes = await octokits.rest.issues.listComments({
        owner,
        repo,
        issue_number: parseInt(prNumber),
      });
      const issueComments = {
        nodes: commentsRes.data.map((c) => ({
          id: String(c.id),
          databaseId: String(c.id),
          body: c.body || "",
          author: { login: (c.user as any)?.login || "" },
          createdAt: c.created_at || "",
          updatedAt: c.updated_at,
        })),
      };

      contextData = {
        title: issue.title || "",
        body: issue.body || "",
        author: { login: (issue.user as any)?.login || "" },
        createdAt: issue.created_at || "",
        updatedAt: issue.updated_at,
        state: (issue.state || "").toUpperCase(),
        comments: issueComments,
      } as GitHubIssue;

      comments = filterCommentsToTriggerTime(issueComments.nodes, triggerTime);

      console.log(`Successfully fetched issue #${prNumber} data`);
    }
  } catch (error) {
    console.error(`Failed to fetch ${isPR ? "PR" : "issue"} data:`, error);
    throw new Error(`Failed to fetch ${isPR ? "PR" : "issue"} data`);
  }


  // Compute SHAs for changed files
  let changedFilesWithSHA: GitHubFileWithSHA[] = [];
  if (isPR && changedFiles.length > 0) {
    changedFilesWithSHA = changedFiles.map((file) => {
      // Don't compute SHA for deleted files
      if (file.changeType === "DELETED") {
        return {
          ...file,
          sha: "deleted",
        };
      }

      try {
        // Use git hash-object to compute the SHA for the current file content
        const sha = execFileSync("git", ["hash-object", file.path], {
          encoding: "utf-8",
        }).trim();
        return {
          ...file,
          sha,
        };
      } catch (error) {
        console.warn(`Failed to compute SHA for ${file.path}:`, error);
        // Return original file without SHA if computation fails
        return {
          ...file,
          sha: "unknown",
        };
      }
    });
  }

  // Prepare all comments for image processing
  const issueComments: CommentWithImages[] = comments
    .filter((c) => c.body && !c.isMinimized)
    .map((c) => ({
      type: "issue_comment" as const,
      id: c.databaseId,
      body: c.body,
    }));

  // Filter review bodies to trigger time
  const filteredReviewBodies = reviewData?.nodes
    ? filterReviewsToTriggerTime(reviewData.nodes, triggerTime).filter(
        (r) => r.body,
      )
    : [];

  const reviewBodies: CommentWithImages[] = filteredReviewBodies.map((r) => ({
    type: "review_body" as const,
    id: r.databaseId,
    pullNumber: prNumber,
    body: r.body,
  }));

  // Filter review comments to trigger time
  const allReviewComments =
    reviewData?.nodes?.flatMap((r) => r.comments?.nodes ?? []) ?? [];
  const filteredReviewComments = filterCommentsToTriggerTime(
    allReviewComments,
    triggerTime,
  );

  const reviewComments: CommentWithImages[] = filteredReviewComments
    .filter((c) => c.body && !c.isMinimized)
    .map((c) => ({
      type: "review_comment" as const,
      id: c.databaseId,
      body: c.body,
    }));

  // Add the main issue/PR body if it has content and wasn't edited after trigger
  // This prevents a TOCTOU race condition where an attacker could edit the body
  // between when an authorized user triggered Letta and when Letta processes the request
  let mainBody: CommentWithImages[] = [];
  if (contextData.body) {
    if (isBodySafeToUse(contextData, triggerTime)) {
      mainBody = [
        {
          ...(isPR
            ? {
                type: "pr_body" as const,
                pullNumber: prNumber,
                body: contextData.body,
              }
            : {
                type: "issue_body" as const,
                issueNumber: prNumber,
                body: contextData.body,
              }),
        },
      ];
    } else {
      console.warn(
        `Security: ${isPR ? "PR" : "Issue"} #${prNumber} body was edited after the trigger event. ` +
          `Excluding body content to prevent potential injection attacks.`,
      );
    }
  }

  const allComments = [
    ...mainBody,
    ...issueComments,
    ...reviewBodies,
    ...reviewComments,
  ];

  const imageUrlMap = await downloadCommentImages(
    octokits,
    owner,
    repo,
    allComments,
  );

  // Fetch trigger user display name if username is provided
  let triggerDisplayName: string | null | undefined;
  if (triggerUsername) {
    triggerDisplayName = await fetchUserDisplayName(octokits, triggerUsername);
  }

  return {
    contextData,
    comments,
    changedFiles,
    changedFilesWithSHA,
    reviewData,
    imageUrlMap,
    triggerDisplayName,
  };
}

export async function fetchUserDisplayName(
  octokits: Octokits,
  login: string,
): Promise<string | null> {
  try {
    const res = await octokits.rest.users.getByUsername({ username: login });
    return (res.data as any).name || null;
  } catch (error) {
    console.warn(`Failed to fetch user display name for ${login}:`, error);
    return null;
  }
}
