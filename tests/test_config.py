from counterfactual_podcast import config


def test_list_ids_present():
    assert config.SYSTEM1_LIST_ID == "683cb9f4387706ad70dc4299"
    assert config.SYSTEM2_LIST_ID == "683cb9e94b55936c9e9505a3"
    assert config.LIFE_OPTIM_LIST_ID == "69cffff85c64bd09a7c8cd7d"
    assert config.TARGET_QUEUE_HOURS == 20


def test_queue_sources_exclude_system2():
    assert config.SYSTEM2_LIST_ID not in config.QUEUE_SOURCE_LIST_IDS
    assert config.SYSTEM1_LIST_ID in config.QUEUE_SOURCE_LIST_IDS
    assert config.LIFE_OPTIM_LIST_ID in config.QUEUE_SOURCE_LIST_IDS


def test_profile_doc_is_scoped():
    assert config.PROFILE_DOC.name.endswith("scoped.md")
