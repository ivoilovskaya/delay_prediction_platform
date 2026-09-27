"""Legacy entry point. New deployments use python -m maintenance.results."""
from maintenance.results import cleanup as cleanup_results, main


def cleanup(now=None, cfg=None):
    """Preserve the old prediction-count return value for existing local callers."""
    from storage.database import DatabaseConfig
    cfg = cfg or DatabaseConfig()
    return cleanup_results(now=now, cfg=cfg).get(cfg.pred_table, 0)


if __name__ == '__main__':
    main()
