@doc raw"""
    acopf_transmission!(EP::Model, inputs::Dict, setup::Dict)

Adds AC optimal power flow constraints when `setup["AC_OPF"] == 1`, using the flow
and loss variables defined in `transmission!`.

Defines zonal voltage magnitudes and angles, resource reactive power limits, active
and reactive line flows, apparent power limits, and zonal reactive power balance.
Line admittances and charging scale with the number of parallel circuits.
Net active power flows enter the zonal power balance in `generate_model`.
"""
function acopf_transmission!(EP::Model, inputs::Dict, setup::Dict)
    println("ACOPF Module")

    gen = inputs["RESOURCES"]
    T = inputs["T"]     # Number of time steps (hours)
    Z = inputs["Z"]     # Number of zones
    G = inputs["G"]     # Number of generators
    L = inputs["L"]     # Number of transmission lines

    # Admittances and taps
    Ys = 1.0 ./ (inputs["Line_Resistance"] .+ im * inputs["Line_Reactance"])
    tap = inputs["Tap_Ratios"] .* exp.(im * inputs["Phase_Shift_Degrees"] * π / 180)
    Yr_base, Yi_base, tapr, tapi, tap2 = real(Ys), imag(Ys), real(tap), imag(tap), abs2.(tap)
    Bc_base = inputs["Line_Susceptance"] # Line charging susceptance, split equally between ends

    # Zonal shunt conductance
    Gs = inputs["Zone_Shunt_Conductance"]

    # Reactive demand (active power balance is handled in generate_model.jl)
    qD = inputs["qD"]

    # Voltage and angle limits
    VoltMagMin = inputs["VoltMagMin"]  # Minimum voltage per zone
    VoltMagMax = inputs["VoltMagMax"]  # Maximum voltage per zone
    AngleDiffLimit = inputs["Line_Angle_Limit"]  # Maximum angle difference per line (radians)

    # Source and sink zone of each line
    pNet_Map = inputs["pNet_Map"]
    from = [findfirst(x->x==1, pNet_Map[l, :]) for l in 1:L]
    to = [findfirst(x->x==-1, pNet_Map[l, :]) for l in 1:L]

    # Reactive-to-active power ratios from minimum power factors
    qfactor_therm = tan(acos(setup["ThermalPowerFactor"]))
    qfactor_inv = tan(acos(setup["InverterPowerFactor"]))

    # VRE availability thresholds for reactive power capability when QVreDeadZone = 1
    qavail_thresh = setup["QAvailThreshold"]  # No Q capability below this availability
    qramp_end = setup["QRampEnd"]  # Full Q capability above this availability

    # Slack angle reference and initial voltage magnitude
    slack_zone = setup["SlackZone"]
    zone_start_kV = inputs["Zone_Start_Voltage_kV"]

    ### Line parameters ###

    @expression(EP, vNumParallel[l=1:L],
        setup["NetworkExpansion"] == 1 ?
        EP[:eAvail_Trans_Cap][l] / inputs["pSingle_Line_Max"][l] :
        inputs["pNum_Parallel"][l]
    )

    # Scale line parameters by number of parallel lines
    @expression(EP, Yr[l=1:L], vNumParallel[l] * Yr_base[l])
    @expression(EP, Yi[l=1:L], vNumParallel[l] * Yi_base[l])
    @expression(EP, Bc[l=1:L], vNumParallel[l] * Bc_base[l])

    # Add shunt reactors as a fraction of adjacent line charging, or use input shunt susceptance
    shunt_fraction = setup["ShuntFraction"]
    if shunt_fraction > 0.0
        println("ShuntFraction=$shunt_fraction: zone shunt susceptances computed from NX-aware line charging.")
        @expression(EP, Bs[z=1:Z],
            -shunt_fraction * sum(Bc[l] / 2 for l in 1:L if from[l] == z || to[l] == z)
        )
    else
        Bs = inputs["Zone_Shunt_Susceptance"]
    end

    # Reactive power for each generator at hour "t", allowing both positive and negative values
    @variable(EP, vQ[y = 1:G, t = 1:T])

    ### Reactive power constraints ###

    # Thermal reactive power limits proportional to installed capacity
    @constraint(EP, cQ_Therm_Lower[y in inputs["THERM_ALL"], t in 1:T],
        vQ[y, t] ≥ -qfactor_therm * EP[:eTotalCap][y])
    @constraint(EP, cQ_Therm_Upper[y in inputs["THERM_ALL"], t in 1:T],
        vQ[y, t] ≤  qfactor_therm * EP[:eTotalCap][y])

    # VRE capability ramps from zero to full between the availability thresholds
    pAvailability = inputs["pP_Max"]
    if setup["QVreDeadZone"] == 1
        qAvailability = min.(1.0, max.(0.0, (pAvailability .- qavail_thresh) ./ (qramp_end - qavail_thresh)))
    else
        qAvailability = ones(size(pAvailability))
    end

    if setup["QInverterDispatchCoupled"] == 1
        println("QInverterDispatchCoupled=1: IBR reactive envelope tied to active dispatch " *
                "(vP), not installed capacity. QVreDeadZone ignored for IBR.")
        @constraint(EP, cQ_VRE_Lower[y in inputs["VRE"], t in 1:T],
            vQ[y, t] ≥ -qfactor_inv * EP[:vP][y, t])
        @constraint(EP, cQ_VRE_Upper[y in inputs["VRE"], t in 1:T],
            vQ[y, t] ≤  qfactor_inv * EP[:vP][y, t])

        # vP is discharge only, so charging or idle storage has no Q capability
        @constraint(EP, cQ_Battery_Lower[y in inputs["STOR_ALL"], t in 1:T],
            vQ[y, t] ≥ -qfactor_inv * EP[:vP][y, t])
        @constraint(EP, cQ_Battery_Upper[y in inputs["STOR_ALL"], t in 1:T],
            vQ[y, t] ≤  qfactor_inv * EP[:vP][y, t])
    else
        # Capacity-based limits; availability derating applies only to VRE
        @constraint(EP, cQ_VRE_Lower[y in inputs["VRE"], t in 1:T],
            vQ[y, t] ≥ -qfactor_inv * EP[:eTotalCap][y] * qAvailability[y, t])
        @constraint(EP, cQ_VRE_Upper[y in inputs["VRE"], t in 1:T],
            vQ[y, t] ≤  qfactor_inv * EP[:eTotalCap][y] * qAvailability[y, t])

        @constraint(EP, cQ_Battery_Lower[y in inputs["STOR_ALL"], t in 1:T],
            vQ[y, t] ≥ -qfactor_inv * EP[:eTotalCap][y])
        @constraint(EP, cQ_Battery_Upper[y in inputs["STOR_ALL"], t in 1:T],
            vQ[y, t] ≤  qfactor_inv * EP[:eTotalCap][y])
    end


    # Forward and reverse active power flow along each line
    @variable(EP, vLineActive[l = 1:L, t = 1:T])
    @variable(EP, vLineActiveRev[l = 1:L, t = 1:T])

    # Forward and reverse reactive power flow along each line
    @variable(EP, vLineReactive[l = 1:L, t = 1:T])
    @variable(EP, vLineReactiveRev[l = 1:L, t = 1:T])

    # Zone voltage magnitude and angle
    @variable(EP, vAngle[z = 1:Z, t = 1:T], start = 0.0)
    @variable(EP, vMag[z = 1:Z, t = 1:T], start = zone_start_kV[z])

    # Slack bus angle reference
    @constraint(EP, cAngleSlack[t = 1:T], vAngle[slack_zone, t] == 0)

    # Voltage magnitude limits
    @constraint(EP, cVoltageMagLimits[z = 1:Z, t = 1:T], VoltMagMin[z] <= vMag[z,t] <= VoltMagMax[z])

    # Voltage angle difference limits across each line
    @constraint(EP, cVoltageAngleDiff[l = 1:L, t = 1:T], -AngleDiffLimit[l] <= vAngle[from[l], t] - vAngle[to[l], t] <= AngleDiffLimit[l])

    ### Helper expressions ###

    # Active generation in each zone "z" at hour "t"
    @expression(EP, eNetActivePower[z = 1:Z, t = 1:T],
        sum(EP[:vP][y, t] for y in resources_in_zone_by_rid(gen, z)))

    # Reactive generation in each zone "z" at hour "t"
    @expression(EP, eNetReactivePower[z = 1:Z, t = 1:T],
        sum(vQ[y, t] for y in resources_in_zone_by_rid(gen, z)))

    # Compute real and imaginary components of voltage
    @expression(EP, eVoltageReal, vMag .* cos.(vAngle))
    @expression(EP, eVoltageImag, vMag .* sin.(vAngle))

    # Pre-calculate cross terms for power flow equations
    @expression(EP, eLineCrossReal,
        eVoltageReal[from, :] .* (Yr .* eVoltageReal[to, :] - Yi .* eVoltageImag[to, :]) +
        eVoltageImag[from, :] .* (Yi .* eVoltageReal[to, :] + Yr .* eVoltageImag[to, :]))

    @expression(EP, eLineCrossRealRev,
        eVoltageReal[to, :] .* (Yr .* eVoltageReal[from, :] - Yi .* eVoltageImag[from, :]) +
        eVoltageImag[to, :] .* (Yi .* eVoltageReal[from, :] + Yr .* eVoltageImag[from, :]))

    @expression(EP, eLineCrossImag,
        -1*(eVoltageReal[from, :] .* (Yi .* eVoltageReal[to, :] + Yr .* eVoltageImag[to, :]) +
        eVoltageImag[from, :] .* (-Yr .* eVoltageReal[to, :] + Yi .* eVoltageImag[to, :]))  )

    @expression(EP, eLineCrossImagRev,
        -1*(eVoltageReal[to, :] .* (Yi .* eVoltageReal[from, :] + Yr .* eVoltageImag[from, :]) +
        eVoltageImag[to, :] .* (-Yr .* eVoltageReal[from, :] + Yi .* eVoltageImag[from, :]))    )

    ### Line flows and limits ###

    # Real part of power flow for each line "l" at time "t"
    @expression(EP, eLineActive, Yr .* (vMag[from, :].^2 ./ tap2) - ((eLineCrossReal .* tapr + eLineCrossImag .* tapi) ./ tap2))
    @constraint(EP, cLineActive, vLineActive .== eLineActive)

    # Real part of power flow for each line "l" at time "t" in reverse direction
    @constraint(EP, cLineActiveRev, vLineActiveRev .== Yr .* (vMag[to, :].^2) - ((eLineCrossRealRev .* tapr - eLineCrossImagRev .* tapi) ./ tap2))

    # Reactive part of power flow for each line "l" at time "t"
    @constraint(EP, cLineReactive, vLineReactive .== (-(Yi .+ Bc ./ 2.0)).*(vMag[from, :].^2)./ tap2 - ((eLineCrossImag .* tapr - eLineCrossReal .* tapi) ./ tap2))

    # Reactive part of power flow for each line "l" at time "t" in reverse direction
    @constraint(EP, cLineReactiveRev, vLineReactiveRev .== -(Yi .+ Bc ./ 2.0).*vMag[to, :].^2 - ((eLineCrossImagRev .* tapr + eLineCrossRealRev .* tapi) ./ tap2))

    # Squared apparent power flow for each line "l" at hour "t"
    @expression(EP, eLineFlowMag[l = 1:L, t = 1:T], vLineActive[l,t]^2 + vLineReactive[l,t]^2)

    # Squared apparent power flow in the reverse direction
    @expression(EP, eLineFlowMagRev[l = 1:L, t = 1:T], vLineActiveRev[l,t]^2 + vLineReactiveRev[l,t]^2)

    # Apparent power limits based on available transmission capacity
    @constraint(EP, cLineFlowLimits[l = 1:L, t = 1:T], eLineFlowMag[l, t] <= EP[:eAvail_Trans_Cap][l]^2)
    @constraint(EP, cLineFlowLimitsRev[l = 1:L, t = 1:T], eLineFlowMagRev[l, t] <= EP[:eAvail_Trans_Cap][l]^2)

    # Link to transmission variables in the model
    @constraint(EP, cLineFlow[l = 1:L, t = 1:T], vLineActive[l, t] == EP[:vFLOW][l, t])
    @constraint(EP, cLineLoss[l = 1:L, t = 1:T], vLineActive[l, t] + vLineActiveRev[l, t] == EP[:vTLOSS][l, t])

    ### Power balance constraints and expressions ###

    # Net active power leaving each zone, subtracted from ePowerBalance in generate_model
    @expression(EP, eActivePowerFlow[t=1:T, z = 1:Z],
        (sum(vLineActive[l, t] for l in 1:L if from[l] == z) +
        sum(vLineActiveRev[l, t] for l in 1:L if to[l] == z) +
        Gs[z]*vMag[z, t]^2)
    )

    @expression(EP, eReactivePowerFlow[z=1:Z, t=1:T],
        sum(vLineReactive[l, t] for l in 1:L if from[l] == z) +
        sum(vLineReactiveRev[l, t] for l in 1:L if to[l] == z) -
        Bs[z]*vMag[z, t]^2
    )

    # Reactive power balance in each zone
    @constraint(EP, cReactivePowerFlow[z = 1:Z, t = 1:T],
        eNetReactivePower[z, t] == qD[t, z] + eReactivePowerFlow[z,t]
    )

end
