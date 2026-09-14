import { Octokit } from "@octokit/rest";
import { GITHUB_API_URL } from "./config";

export type Octokits = {
  rest: Octokit;
};

export function createOctokit(token: string): Octokits {
  return {
    rest: new Octokit({
      auth: token,
      baseUrl: GITHUB_API_URL,
    }),
  };
}
