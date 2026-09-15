# The shared dependency prefix also contains USD 23.08 headers. Put this
# overlay's headers first, including before transitive TBB/Boost includes.
include_directories(BEFORE SYSTEM "$ENV{USD25_ROOT}/include")
