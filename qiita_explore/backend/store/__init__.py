"""store — public API for the SQLite project/study/chat store.

Import from here:  from store import get_project, list_projects, ...
"""

from .crud import (  # noqa: F401
    PROJECT_STUDIES_CAP,
    list_projects,
    create_project,
    get_project,
    get_project_studies_only,
    get_project_id_for_chat,
    allowed_project_study_ids,
    update_project,
    delete_project,
    add_study_to_project,
    remove_study_from_project,
    list_chats,
    get_chat,
    project_chat_exists,
    create_chat,
    append_chat_messages,
    update_chat_title,
    set_chat_pinned,
    set_chat_archived,
    delete_chat,
)

from .global_chat_crud import (  # noqa: F401
    list_global_chats,
    get_global_chat,
    global_chat_exists,
    create_global_chat,
    append_global_chat_messages,
    update_global_chat_title,
    set_global_chat_pinned,
    set_global_chat_archived,
    delete_global_chat,
)

from .chat_move import (  # noqa: F401
    move_chat_to_project,
    move_global_chat_to_project,
    move_project_chat_to_global,
)

from .merge_crud import (  # noqa: F401
    create_workspace,
    list_workspaces,
    get_workspace,
    delete_workspace,
    rename_workspace,
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
    update_project_study_data,
    get_study_detail_cache,
    upsert_study_detail_cache,
    get_biom_sample_cache,
    upsert_biom_sample_cache,
    pin_study_to_chat,
    unpin_study_from_chat,
    list_pinned_studies,
    list_pinned_study_meta,
)
