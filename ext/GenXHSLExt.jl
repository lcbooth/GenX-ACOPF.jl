module GenXHSLExt

using GenX
using HSL_jll

# Register the HSL library for Ipopt when GenX and HSL_jll are loaded
function __init__()
    GenX.HSL_LIBRARY_PATH[] = HSL_jll.libhsl_path
end

end
