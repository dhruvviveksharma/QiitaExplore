"""store — public API for the SQLite project/study/chat store.

Import from here:  from store import get_project, list_projects, ...
"""

from .crud import (  # noqa: F401
    list_projects,
    create_project,
    get_project,
    get_project_studies_only,
    update_project,
    delete_project,
    add_study_to_project,
    remove_study_from_project,
    list_chats,
    get_chat,
    create_chat,
    append_chat_messages,
    delete_chat,
    list_global_chats,
    get_global_chat,
    create_global_chat,
    append_global_chat_messages,
    delete_global_chat,
)

from .merge_crud import (  # noqa: F401
    create_workspace,
    list_workspaces,
    get_workspace,
    delete_workspace,
    add_study_to_workspace,
    remove_study_from_workspace,
    update_workspace_study,
    create_merge_job,
    get_merge_job,
    list_merge_jobs,
    update_merge_job_status,
)

from .cache import (  # noqa: F401
    SCOPE_PROJECT,
    SCOPE_GLOBAL,
    PINNED_STUDIES_PER_CHAT_CAP,
    upsert_project_study_summary,
    get_project_context_summary,
    upsert_project_context_summary,
    update_project_study_data,
    get_study_detail_cache,
    upsert_study_detail_cache,
    pin_study_to_chat,
    unpin_study_from_chat,
    list_pinned_studies,
)
