// GPU executables are software, not downloadable model data.
class GpuBinaryManager {
  constructor(config) {
    this.config = config;
  }

  isSupported() {
    return false;
  }

  getBinaryPath() {
    return null;
  }

  isDownloaded() {
    return false;
  }

  needsUpdate() {
    return false;
  }

  isDownloading() {
    return false;
  }

  getStatus() {
    return { supported: false, downloaded: false, downloading: false };
  }

  async download() {
    throw new Error("Install and update local inference engines with pacman.");
  }

  cancelDownload() {
    return false;
  }

  async delete() {
    return { deletedCount: 0, freedBytes: 0 };
  }
}

module.exports = GpuBinaryManager;
// Do not migrate or delete engines downloaded by another installation.
module.exports.migrateLegacyBinDir = () => [];
module.exports.detectOrphanedGpuPacks = () => [];
module.exports.detectOutdatedGpuPacks = () => [];
