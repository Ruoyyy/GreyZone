/* eslint-disable global-require, @typescript-eslint/no-require-imports */
// Deleting a pod and everything in it. Shared by the human route
// (podController.deletePod) and the approved `delete_pod` card
// (approvalActionService) so both remove exactly the same set of rows.
//
// Agent User rows are deliberately NOT touched — identity survives the pod
// (CLAUDE.md design rule 8); only the pod-scoped installations go.
export const deletePodWithContents = async (podId: string): Promise<void> => {
  const Pod = require('../models/Pod');
  const Message = require('../models/Message');
  const Post = require('../models/Post');
  const Summary = require('../models/Summary');
  const PodAsset = require('../models/PodAsset');
  const Integration = require('../models/Integration');
  const { AgentInstallation } = require('../models/AgentRegistry');
  const AgentProfile = require('../models/AgentProfile');

  await Message.deleteMany({ podId });
  if (process.env.PG_HOST) {
    await require('../models/pg/Message').deleteByPodId(podId);
  }

  await Promise.allSettled([
    Post.deleteMany({ podId }),
    Summary.deleteMany({ podId }),
    PodAsset.deleteMany({ podId }),
    Integration.deleteMany({ podId }),
    AgentInstallation.deleteMany({ podId }),
    AgentProfile.deleteMany({ podId }),
  ]);

  if (process.env.PG_HOST) {
    await require('../models/pg/Pod').delete(podId);
  }

  await Pod.deleteOne({ _id: podId });
};
