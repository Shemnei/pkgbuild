// Pacman is the only software updater in this distribution.
const message = "OpenWhispr is managed by pacman; update it with pacman.";

class UpdateManager {
  constructor() {
    this.isQuittingForUpdate = false;
  }

  setWindowManager() {}
  setAutoUpdatesEnabled() {}
  checkForUpdatesOnStartup() {}
  deferInstallOnQuit() {}
  cleanup() {}

  hasStagedUpdate() {
    return false;
  }

  async checkForUpdates() {
    return { updateAvailable: false, message };
  }

  async downloadUpdate() {
    return { success: false, message };
  }

  async installUpdate() {
    return { success: false, message };
  }

  async getAppVersion() {
    return { version: require("electron").app.getVersion() };
  }

  async getUpdateStatus() {
    return {
      updateAvailable: false,
      updateDownloaded: false,
      isDevelopment: false,
      isSupported: false,
    };
  }

  async getUpdateInfo() {
    return null;
  }
}

module.exports = UpdateManager;
