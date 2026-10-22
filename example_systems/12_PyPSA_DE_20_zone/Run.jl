# Solves this case (settings/genx_settings.yml) with each transmission model.
# From the repository root:
#
#   julia --project=. example_systems/<case>/Run.jl                  # transport, dc and ac
#   julia --project=. example_systems/<case>/Run.jl ac               # or any subset
#
# Results are written to results/<model>/. The time series are clustered on the first run
# and the TDR_results/ folder is reused by later runs.
ENV["GENX_PRECOMPILE"] = "false"
using GenX
using HiGHS
using Ipopt

# HSL MA86 linear solver for Ipopt. Optional: HSL_jll needs a (free academic)
# licence from https://licences.stfc.ac.uk/product/julia-hsl. Without it Ipopt
# uses MUMPS, which is slower.
try
    @eval using HSL_jll
catch
    @warn "HSL_jll not available; Ipopt will use MUMPS instead of MA86"
end

case = dirname(@__FILE__)
models = isempty(ARGS) ? ["transport", "dc", "ac"] : ARGS

for model in models
    model in ("transport", "dc", "ac") || error("Unknown model '$model'. Use transport, dc or ac.")
    setup = GenX.configure_settings(GenX.get_settings_path(case, "genx_settings.yml"),
        GenX.get_settings_path(case, "output_settings.yml"))
    setup["DC_OPF"] = model == "dc" ? 1 : 0
    setup["AC_OPF"] = model == "ac" ? 1 : 0

    # Transport is an LP. DCOPF with network expansion and ACOPF are both nonlinear
    # and nonconvex, so both are solved with Ipopt.
    optimizer = model == "transport" ? HiGHS.Optimizer : Ipopt.Optimizer

    println("\n=== $(case): $(model) ===")
    GenX.run_genx_case_simple!(case, setup, optimizer; outpath = joinpath(case, "results", model))
end
