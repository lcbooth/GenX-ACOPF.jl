module TestExamples

using Test
using GenX
using JuMP
using HiGHS
using YAML

# Test that the examples in the example_systems directory run without error
function test_examples()
    base_path = Base.dirname(Base.dirname(pathof(GenX)))
    examples_path = joinpath(base_path, "example_systems")
    highs_version = VersionNumber(MOI.get(HiGHS.Optimizer(), MOI.SolverVersion()))

    examples_dir = readdir(examples_path, join = true)
    for example_dir in examples_dir
        if isdir(example_dir) && isfile(joinpath(example_dir, "Run.jl"))
            isempty(ARGS) || basename(example_dir) in ARGS || continue
            # Cases requiring external data builds are run separately
            input_paths = [joinpath(example_dir, "system", "Demand_data.csv"),
                joinpath(example_dir, "inputs", "inputs_p1", "system", "Demand_data.csv")]
            if !any(isfile, input_paths)
                @info "Skipping example without bundled inputs: $example_dir"
                continue
            end
            @info "Running example in $example_dir"
            @testset "$(basename(example_dir))" begin
                mktempdir() do directory
                    case = joinpath(directory, basename(example_dir))
                    mkdir(case)
                    for folder in ("settings", "system", "resources", "inputs", "policies")
                        source = joinpath(example_dir, folder)
                        isdir(source) && cp(source, joinpath(case, folder))
                    end
                    # Keep the legacy IPX algorithm when ipm selects HiPO in newer HiGHS
                    if highs_version >= v"1.15"
                        solver_path = joinpath(case, "settings", "highs_settings.yml")
                        solver_settings = YAML.load_file(solver_path)
                        if get(solver_settings, "Method", "ipm") == "ipm"
                            solver_settings["Method"] = "ipx"
                            YAML.write_file(solver_path, solver_settings)
                        end
                    end
                    result = run_genx_case!(case)
                    if result isa Tuple
                        model, setup, inputs = result
                        @test termination_status(model) == MOI.OPTIMAL
                        @test primal_status(model) == MOI.FEASIBLE_POINT
                        @test isfinite(objective_value(model))
                    else
                        # Multi-stage runs write their results and return nothing
                        @test isnothing(result)
                    end
                end
            end
        end
    end
end

@testset "Test examples" begin
    test_examples()
end

end # module
