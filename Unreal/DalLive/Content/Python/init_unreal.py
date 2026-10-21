# Runs when the editor starts with the DalLive plugin enabled (PythonScriptPlugin runs init_unreal.py of every plugin).
import unreal

try:
    import dal_live

    if not dal_live.install() and "not available yet" in (dal_live.connect_error or ""):
        # The subsystem can come up a little later than Python: try again from the first Slate ticks.
        _retry = {}

        def _retry_install(_dt):
            if dal_live.install():
                unreal.unregister_slate_post_tick_callback(_retry["handle"])

        _retry["handle"] = unreal.register_slate_post_tick_callback(_retry_install)
except Exception as _e:  # never take the editor start down
    unreal.log_error(f"DalLive: cannot start: {type(_e).__name__}: {_e}")
