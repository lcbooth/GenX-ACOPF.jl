module TestACOPF

using Test
using GenX
using JuMP
using Ipopt

# Nine-bus DCOPF fixture extended with AC network inputs
test_path = joinpath(@__DIR__, "ACOPF")
genx_setup = GenX.default_settings()
merge!(genx_setup, Dict("AC_OPF" => 1,
    "MeanPowerFactor" => 0.95,
    "PowerFactorSpread" => 0.0,
    "ThermalPowerFactor" => 0.8,
    "ShuntFraction" => 1.0,
    "EnableJuMPDirectModel" => false))

EP, inputs = redirect_stdout(devnull) do
    inputs = load_inputs(genx_setup, test_path)
    optimizer = configure_solver(test_path, Ipopt.Optimizer)
    model = generate_model(genx_setup, inputs, optimizer)
    model, _ = solve_model(model, genx_setup)
    model, inputs
end

@test termination_status(EP) in (MOI.OPTIMAL, MOI.LOCALLY_SOLVED)
@test primal_status(EP) == MOI.FEASIBLE_POINT
obj_true = 419.977446
@test objective_value(EP) ≈ obj_true rtol=1e-5

# Active and reactive power balance, with all demand served
tolerance = 1e-4
@test maximum(abs.(value.(EP[:ePowerBalance]) .- inputs["pD"])) <= tolerance
reactive_residual = value.(EP[:eNetReactivePower]) .-
    permutedims(inputs["qD"]) .- value.(EP[:eReactivePowerFlow])
@test maximum(abs.(reactive_residual)) <= tolerance
@test sum(value.(EP[:vNSE])) <= tolerance

# Voltage bounds, reference angle, and apparent power limits at both line ends
voltage = value.(EP[:vMag])
@test all(voltage .>= inputs["VoltMagMin"] .- 1e-5)
@test all(voltage .<= inputs["VoltMagMax"] .+ 1e-5)
@test abs(value(EP[:vAngle][genx_setup["SlackZone"], 1])) <= 1e-6
capacity = value.(EP[:eAvail_Trans_Cap])
forward = value.(EP[:vLineActive])
reverse = value.(EP[:vLineActiveRev])
@test all(hypot.(forward, value.(EP[:vLineReactive])) .<= capacity .+ tolerance)
@test all(hypot.(reverse, value.(EP[:vLineReactiveRev])) .<= capacity .+ tolerance)

# Line losses and total active power conservation
losses = value.(EP[:vTLOSS])
@test minimum(losses) >= -tolerance
@test sum(losses) > tolerance
@test maximum(abs.(losses .- forward .- reverse)) <= tolerance
@test sum(value.(EP[:vP])) ≈ sum(inputs["pD"]) + sum(losses) atol=tolerance

end # module TestACOPF
