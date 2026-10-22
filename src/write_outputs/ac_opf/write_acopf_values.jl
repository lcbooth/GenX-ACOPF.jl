@doc raw"""
    write_acopf_values(path::AbstractString, inputs::Dict, setup::Dict, EP::Model)

Write ACOPF results to three CSV files in `path`:
 - `generator_records.csv`: active and reactive output and capacity of each resource
 - `zonal_records.csv`: voltage magnitude (kV) and angle (degrees), and the active and reactive
   generation, net network flows and demand of each zone
 - `line_records.csv`: active and reactive flows at both ends of each line, apparent power
   capacity and losses
"""
function write_acopf_values(path::AbstractString,
        inputs::Dict,
        setup::Dict,
        EP::Model)
    mkpath(path)

    scale_factor = setup["ParameterScale"] == 1 ? ModelScalingFactor : 1

    Z = inputs["Z"]     # Number of zones
    T = inputs["T"]     # Number of time steps
    L = inputs["L"]     # Number of lines
    G = inputs["G"]     # Number of generators
    gen_names = inputs["RESOURCE_NAMES"] # Resource names

    # Real and reactive demand
    pD = inputs["pD"] * scale_factor
    qD = inputs["qD"] * scale_factor

    # Source and sink zone of each line
    pNet_Map = inputs["pNet_Map"]
    from = [findfirst(x -> x == 1, pNet_Map[l, :]) for l in 1:L]
    to = [findfirst(x -> x == -1, pNet_Map[l, :]) for l in 1:L]

    LineActive = value.(EP[:vLineActive]) * scale_factor  # Forward active power flow
    LineReactive = value.(EP[:vLineReactive]) * scale_factor # Forward reactive power flow
    LineActiveRev = value.(EP[:vLineActiveRev]) * scale_factor # Reverse active power flow
    LineReactiveRev = value.(EP[:vLineReactiveRev]) * scale_factor # Reverse reactive power flow
    trans_cap = value.(EP[:eAvail_Trans_Cap]) * scale_factor # Available transmission capacity
    tloss = value.(EP[:vTLOSS]) * scale_factor # Transmission losses

    eNetActive = value.(EP[:eNetActivePower]) * scale_factor # Net active generation in each zone
    eNetReactive = value.(EP[:eNetReactivePower]) * scale_factor # Net reactive generation in each zone
    vMag = value.(EP[:vMag])  # Voltage magnitude
    vAngle = value.(EP[:vAngle])  # Voltage angle
    vP = value.(EP[:vP]) * scale_factor # Active power of each resource
    vQ = value.(EP[:vQ]) * scale_factor # Reactive power of each resource
    eActivePowerFlow = value.(EP[:eActivePowerFlow]) * scale_factor # Net active power flow out of each zone
    eReactivePowerFlow = value.(EP[:eReactivePowerFlow]) * scale_factor # Net reactive power flow out of each zone
    eTotalCap = value.(EP[:eTotalCap]) * scale_factor

    generator_records = [(Time = t,
                             Generator = g,
                             Name = gen_names[g],
                             P = vP[g, t],
                             Q = vQ[g, t],
                             Cap = eTotalCap[g])
                         for t in 1:T for g in 1:G]

    zone_records = [(Time = t,
                        Zone = z,
                        VoltageMag = vMag[z, t],
                        VoltageAngle = vAngle[z, t] * 180 / π,
                        NetGenP = eNetActive[z, t],
                        NetFlowP = eActivePowerFlow[t, z],
                        DemandP = pD[t, z],
                        NetGenQ = eNetReactive[z, t],
                        NetFlowQ = eReactivePowerFlow[z, t],
                        DemandQ = qD[t, z])
                    for t in 1:T for z in 1:Z]

    line_records = [(Time = t,
                        Line = l,
                        From = from[l],
                        To = to[l],
                        ApparentFlowCap = trans_cap[l],
                        ActiveFlow_from = LineActive[l, t],
                        ActiveFlow_to = LineActiveRev[l, t],
                        ReactiveFlow_from = LineReactive[l, t],
                        ReactiveFlow_to = LineReactiveRev[l, t],
                        Loss = tloss[l, t],
                        Percent_Loss = abs(tloss[l, t] / LineActive[l, t] * 100))
                    for t in 1:T for l in 1:L]

    CSV.write(joinpath(path, "generator_records.csv"), DataFrame(generator_records))
    CSV.write(joinpath(path, "zonal_records.csv"), DataFrame(zone_records))
    CSV.write(joinpath(path, "line_records.csv"), DataFrame(line_records))
    println("ACOPF records saved to: ", path)

    return nothing
end
