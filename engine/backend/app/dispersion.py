"""Idealized open-terrain forward model; not validated for skid obstruction."""
import math

def concentration_ppm(q_kg_h, dx, dy, z, height, wind_speed, wind_from_degrees, temperature_k=293.15, pressure_pa=101325.):
    if q_kg_h < 0 or temperature_k <= 0 or pressure_pa <= 0:
        raise ValueError("invalid physical inputs")
    if wind_speed < 0.5:
        raise ValueError("low wind violates steady plume assumptions")
    # ENU coordinates, meteorological direction FROM north clockwise.
    angle = math.radians(wind_from_degrees)
    x = -dx*math.sin(angle)-dy*math.cos(angle)
    y = dx*math.cos(angle)-dy*math.sin(angle)
    if x <= 0: return 0.
    sy, sz = max(.5, .12*x), max(.4, .08*x)
    kg_m3 = (q_kg_h/3600)/(2*math.pi*wind_speed*sy*sz)*math.exp(-y*y/(2*sy*sy))*(math.exp(-(z-height)**2/(2*sz*sz))+math.exp(-(z+height)**2/(2*sz*sz)))
    return kg_m3 * 8.314462618*temperature_k/(pressure_pa*.01604)*1e6
