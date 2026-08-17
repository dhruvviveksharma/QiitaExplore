# Shared env detection for QiitaExplore start scripts.
# Source after SCRIPT_DIR is set to qiita_explore/.
# Rule: branch master → deployment (port 5001); anything else → dev (port 5002).
# Detached HEAD and non-master both fall through to dev so we never silently
# claim the deployment port/data without positively being on master.
#
# Use (cd … && git …) instead of `git -C` — barnacle's git is old enough that
# -C is unreliable / missing, which previously always fell through to "HEAD".

BRANCH="$(cd "$SCRIPT_DIR" && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "HEAD")"
if [ "$BRANCH" = "master" ]; then
  QE_ENV_NAME="deployment"
  QE_PORT=5001
else
  QE_ENV_NAME="dev"
  QE_PORT=5002
fi
QE_DATA_ROOT="/ddn_scratch/d4sharma/QiitaExploreDB/$QE_ENV_NAME"
echo "Detected branch '$BRANCH' -> $QE_ENV_NAME (port $QE_PORT, data root $QE_DATA_ROOT)"
