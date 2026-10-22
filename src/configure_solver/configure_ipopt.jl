# HSL library path registered by GenXHSLExt when HSL_jll is loaded
const HSL_LIBRARY_PATH = Ref{Union{Nothing, String}}(nothing)

@doc raw"""
	configure_ipopt(solver_settings_path::String, optimizer::Any)

Reads user-specified solver settings from ipopt\_settings.yml in the directory specified by the string solver\_settings\_path.

Returns a `MathOptInterface.OptimizerWithAttributes` Ipopt optimizer instance to be used in the `GenX.generate_model()` method.

Settings are passed directly to Ipopt (see https://coin-or.github.io/Ipopt/OPTIONS.html).
If `linear_solver` is omitted, uses HSL MA86 with MC64 scaling when HSL_jll is
loaded, otherwise MUMPS. `OMP_NUM_THREADS` controls MA86 threading.

`print_level` defaults to 5.
"""
function configure_ipopt(solver_settings_path::String, optimizer::Any)
    solver_settings = YAML.load(open(solver_settings_path))
    solver_settings = convert(Dict{String, Any}, solver_settings)

    if !haskey(solver_settings, "linear_solver") && !isnothing(HSL_LIBRARY_PATH[])
        solver_settings["linear_solver"] = "ma86"
        solver_settings["ma86_scaling"] = "mc64"
    end
    # HSL solvers (ma27, ma57, ma86, ma97) need the library path
    if startswith(get(solver_settings, "linear_solver", ""), "ma") &&
       !haskey(solver_settings, "hsllib")
        if isnothing(HSL_LIBRARY_PATH[])
            error("Ipopt linear_solver $(solver_settings["linear_solver"]) requires HSL. " *
                  "Load it with `using HSL_jll` or set `hsllib` in ipopt_settings.yml.")
        end
        solver_settings["hsllib"] = HSL_LIBRARY_PATH[]
    end
    get!(solver_settings, "print_level", 5)

    return optimizer_with_attributes(optimizer, solver_settings...)
end
