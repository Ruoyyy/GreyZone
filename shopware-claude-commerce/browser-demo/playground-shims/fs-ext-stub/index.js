'use strict';
// No-op flock/fcntl stub so @php-wasm/node loads on Node versions
// without a matching fs-ext prebuild. Playground PHP uses MEMFS.

exports.constants = {
  LOCK_SH: 1,
  LOCK_EX: 2,
  LOCK_NB: 4,
  LOCK_UN: 8,
  F_GETLK: 5,
  F_SETLK: 6,
  F_SETLKW: 7,
  LOCKFILE_FAIL_IMMEDIATELY: 1,
  LOCKFILE_EXCLUSIVE_LOCK: 2,
};

function noop() { return 0; }
exports.flock = function (fd, flags, cb) { if (cb) cb(null, 0); };
exports.flockSync = noop;
exports.fcntl = function (fd, cmd, arg, cb) { if (cb) cb(null, 0); };
exports.fcntlSync = noop;
exports.seek = function (fd, offset, whence, cb) { if (cb) cb(null, 0); };
exports.seekSync = noop;
exports.statVFS = function () { return {}; };
exports.lockFileEx = function (fd, flags, a, b, c, d, cb) { if (cb) cb(null); };
exports.lockFileExSync = noop;
exports.unlockFileEx = function (fd, a, b, c, d, cb) { if (cb) cb(null); };
exports.unlockFileExSync = noop;
exports.useNativeModule = function () {};
exports.getNativeModuleSource = function () { return 'stub'; };
