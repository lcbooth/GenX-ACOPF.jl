function get_settings_path(case::AbstractString)
    return joinpath(case, "settings")
end

function get_settings_path(case::AbstractString, filename::AbstractString)
    return joinpath(get_settings_path(case), filename)
end

function get_default_output_folder(case::AbstractString)
    return joinpath(case, "results")
end

function get_timestamp_folder(case::AbstractString, prefix::String = "results")
    timestamp = Dates.format(Dates.now(), "yyyy-mm-dd_HHMMSS")
    return joinpath(case, prefix, timestamp)
end

@doc raw"""
    run_genx_case!(case::AbstractString, optimizer::Any=HiGHS.Optimizer)

Run a GenX case with the specified optimizer. The optimizer can be any solver supported by MathOptInterface.

# Arguments
- `case::AbstractString`: the path to the case folder
- `optimizer::Any`: the optimizer instance to be used in the optimization model

# Example
```julia
run_genx_case!("path/to/case", HiGHS.Optimizer)
```

```julia
run_genx_case!("path/to/case", Gurobi.Optimizer)
```
"""
function run_genx_case!(case::AbstractString, optimizer::Any = HiGHS.Optimizer)
    print_genx_version() # Log the GenX version
    genx_settings = get_settings_path(case, "genx_settings.yml") # Settings YAML file path
    writeoutput_settings = get_settings_path(case, "output_settings.yml") # Write-output settings YAML file path
    mysetup = configure_settings(genx_settings, writeoutput_settings) # mysetup dictionary stores settings and GenX-specific parameters

    if mysetup["MultiStage"] == 0
        return run_genx_case_simple!(case, mysetup, optimizer)
    else
        run_genx_case_multistage!(case, mysetup, optimizer)
    end
end

function time_domain_reduced_files_exist(tdrpath)
    tdr_demand = file_exists(tdrpath, ["Demand_data.csv", "Load_data.csv"])
    tdr_genvar = isfile(joinpath(tdrpath, "Generators_variability.csv"))
    tdr_fuels = isfile(joinpath(tdrpath, "Fuels_data.csv"))
    return (tdr_demand && tdr_genvar && tdr_fuels)
end

@doc raw"""
    run_genx_case_simple!(case::AbstractString, mysetup::Dict, optimizer::Any; outpath=nothing)

Run a single-stage GenX case and return `(EP, mysetup, myinputs)`.

Writes results to `outpath` or a timestamped folder under `<case>/results/`.
Saves copies of network, demand, and any reduced time series inputs with the results.
"""
function run_genx_case_simple!(case::AbstractString, mysetup::Dict, optimizer::Any;
        outpath::Union{AbstractString, Nothing} = nothing)
    settings_path = get_settings_path(case)

    ### Cluster time series inputs if necessary and if specified by the user
    if mysetup["TimeDomainReduction"] == 1
        TDRpath = joinpath(case, mysetup["TimeDomainReductionFolder"])
        system_path = joinpath(case, mysetup["SystemFolder"])
        prevent_doubled_timedomainreduction(system_path)
        # Existing TDR files are reused even if inputs have changed
        if !time_domain_reduced_files_exist(TDRpath)
            println("Clustering Time Series Data (Grouped)...")
            cluster_inputs(case, settings_path, mysetup)
        else
            println("Time Series Data Already Clustered.")
        end
    end

    ### Configure solver
    println("Configuring Solver")
    solver_name = lowercase(get(mysetup, "Solver", ""))
    OPTIMIZER = configure_solver(settings_path, optimizer; solver_name=solver_name)

    #### Running a case

    ### Load inputs
    println("Loading Inputs")
    myinputs = load_inputs(mysetup, case)

    println("Generating the Optimization Model")
    time_elapsed = @elapsed EP = generate_model(mysetup, myinputs, OPTIMIZER)
    println("Time elapsed for model building is")
    println(time_elapsed)

    println("Solving Model")
    EP, solve_time = solve_model(EP, mysetup)
    myinputs["solve_time"] = solve_time # Store the model solve time in myinputs

    # Run MGA if the MGA flag is set to 1 else only save the least cost solution
    if has_values(EP)
        println("Writing Output")
        outputs_path = isnothing(outpath) ? get_timestamp_folder(case) : outpath
        elapsed_time = @elapsed begin
            result_path = write_outputs(EP, outputs_path, mysetup, myinputs)
            !isnothing(result_path) && (outputs_path = result_path)
        end

        println("Time elapsed for writing is")
        println(elapsed_time)
        println("Output path, ", outputs_path)

        # Save copies of key inputs alongside the results
        input_path = joinpath(outputs_path, "input_data")
        mkpath(input_path)
        system_dir = joinpath(case, mysetup["SystemFolder"])
        for fname in ["Network.csv", "Demand_data.csv"]
            src = joinpath(system_dir, fname)
            if isfile(src)
                cp(src, joinpath(input_path, fname); force = true)
            else
                @warn "Missing file: $src"
            end
        end

        if mysetup["AC_OPF"] == 1
            println("Writing ACOPF Outputs")
            write_acopf_values(joinpath(outputs_path, "acopf"), myinputs, mysetup, EP)
        end

        # Copy reduced time series; retain the source folder for other runs
        if mysetup["TimeDomainReduction"] == 1
            TDRpath = joinpath(case, mysetup["TimeDomainReductionFolder"])
            if isdir(TDRpath)
                tdr_copy_target = joinpath(outputs_path, basename(TDRpath))
                cp(TDRpath, tdr_copy_target; force = true)
                println("Copied clustered TDR files to: ", tdr_copy_target)
            else
                @warn "No time domain reduction folder found at $TDRpath"
            end
        end

        if mysetup["ModelingToGenerateAlternatives"] == 1
            println("Starting Model to Generate Alternatives (MGA) Iterations")
            mga(EP, case, mysetup, myinputs)
        end

        if mysetup["MethodofMorris"] == 1
            println("Starting Global sensitivity analysis with Method of Morris")
            morris(EP, case, mysetup, myinputs, outputs_path, OPTIMIZER)
        end
    end

    return EP, mysetup, myinputs
end

function run_genx_case_multistage!(case::AbstractString, mysetup::Dict, optimizer::Any)
    settings_path = get_settings_path(case)
    multistage_settings = get_settings_path(case, "multi_stage_settings.yml") # Multi stage settings YAML file path
    # merge default settings with those specified in the YAML file
    mysetup["MultiStageSettingsDict"] = configure_settings_multistage(multistage_settings)

    ### Cluster time series inputs if necessary and if specified by the user
    if mysetup["TimeDomainReduction"] == 1
        tdr_settings = get_settings_path(case, "time_domain_reduction_settings.yml") # Multi stage settings YAML file path
        TDRSettingsDict = YAML.load(open(tdr_settings))

        first_stage_path = joinpath(case, "inputs", "inputs_p1")
        TDRpath = joinpath(first_stage_path, mysetup["TimeDomainReductionFolder"])
        system_path = joinpath(first_stage_path, mysetup["SystemFolder"])
        prevent_doubled_timedomainreduction(system_path)
        if !time_domain_reduced_files_exist(TDRpath)
            if (mysetup["MultiStage"] == 1) &&
               (TDRSettingsDict["MultiStageConcatenate"] == 0)
                println("Clustering Time Series Data (Individually)...")
                for stage_id in 1:mysetup["MultiStageSettingsDict"]["NumStages"]
                    cluster_inputs(case, settings_path, mysetup, stage_id)
                end
            else
                println("Clustering Time Series Data (Grouped)...")
                cluster_inputs(case, settings_path, mysetup)
            end
        else
            println("Time Series Data Already Clustered.")
        end
    end

    ### Configure solver
    println("Configuring Solver")
    solver_name = lowercase(get(mysetup, "Solver", ""))
    OPTIMIZER = configure_solver(settings_path, optimizer; solver_name=solver_name)

    model_dict = Dict()
    inputs_dict = Dict()

    for t in 1:mysetup["MultiStageSettingsDict"]["NumStages"]

        # Step 0) Set Model Year
        mysetup["MultiStageSettingsDict"]["CurStage"] = t

        # Step 1) Load Inputs
        inpath_sub = joinpath(case, "inputs", string("inputs_p", t))

        inputs_dict[t] = load_inputs(mysetup, inpath_sub)
        inputs_dict[t] = configure_multi_stage_inputs(inputs_dict[t],
            mysetup["MultiStageSettingsDict"],
            mysetup["NetworkExpansion"])

        compute_cumulative_min_retirements!(inputs_dict, t)
        # Step 2) Generate model
        model_dict[t] = generate_model(mysetup, inputs_dict[t], OPTIMIZER)
    end

    # check that resources do not switch from can_retire = 0 to can_retire = 1 between stages
    validate_can_retire_multistage(
        inputs_dict, mysetup["MultiStageSettingsDict"]["NumStages"])

    # Prepare folder for results    
    outpath = get_default_output_folder(case)

    if mysetup["OverwriteResults"] == 1
        # Overwrite existing results if dir exists
        # This is the default behaviour when there is no flag, to avoid breaking existing code
        if !(isdir(outpath))
            mkdir(outpath)
        end
    else
        # Find closest unused ouput directory name and create it
        outpath = choose_output_dir(outpath)
        mkdir(outpath)
    end

    ### Solve model
    println("Solving Model")

    # Step 3) Run DDP Algorithm or Myopic single pass
    if mysetup["MultiStageSettingsDict"]["Myopic"] == 1
        mystats_d = Dict()  # mystats_d is for DDP iteration metadata
        model_dict, inputs_dict = run_myopic_multistage(outpath, model_dict, mysetup, inputs_dict)
    else
        model_dict, mystats_d, inputs_dict = run_ddp(outpath, model_dict, mysetup, inputs_dict)
    end

    # Step 4) Write final outputs from each stage
    if mysetup["MultiStageSettingsDict"]["Myopic"] == 0 ||
       mysetup["MultiStageSettingsDict"]["WriteIntermittentOutputs"] == 0
        for p in 1:mysetup["MultiStageSettingsDict"]["NumStages"]
            mysetup["MultiStageSettingsDict"]["CurStage"] = p
            outpath_cur = joinpath(outpath, "results_p$p")
            write_outputs(model_dict[p], outpath_cur, mysetup, inputs_dict[p])
        end
    end

    # Step 5) Write DDP summary outputs

    write_multi_stage_outputs(mystats_d, outpath, mysetup, inputs_dict)
end
