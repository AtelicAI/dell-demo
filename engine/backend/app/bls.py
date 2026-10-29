"""Backward Lagrangian stochastic (bLs) unit response for point sources in a horizontally homogeneous surface layer.

Method follows Thomson (1987) for Gaussian inhomogeneous turbulence with correlated u'w', run backward in time
as in Flesch, Wilson and Yee (1995) and Flesch et al. (2004), the model family implemented by WindTrax.
Turbulence profiles are Monin-Obukhov similarity forms. Experimental: not calibrated, not validated against WindTrax.
"""
import math
import numpy as np

KARMAN=.4
C0=3.1
METHOD='backward-lagrangian-stochastic-v0'


def psi_m(zeta):
    zeta=np.asarray(zeta,float)
    x=np.power(np.maximum(1-16*np.minimum(zeta,0),1),.25)
    unstable=2*np.log((1+x)/2)+np.log((1+x*x)/2)-2*np.arctan(x)+math.pi/2
    return np.where(zeta<0,unstable,-5*np.maximum(zeta,0))


def phi_m(zeta):
    zeta=np.asarray(zeta,float)
    return np.where(zeta<0,np.power(np.maximum(1-16*np.minimum(zeta,0),1),-.25),1+5*np.maximum(zeta,0))


class SurfaceLayer:
    """MOST profiles. L=inf means neutral. sigma_u, sigma_v are height-independent."""
    def __init__(self,ustar,obukhov,z0,sigma_u=None,sigma_v=None,boundary_layer_m=1000.):
        if not (ustar>0 and z0>0 and math.isfinite(ustar) and math.isfinite(z0)):raise ValueError('ustar and z0 must be positive')
        if obukhov==0 or math.isnan(obukhov):raise ValueError('Obukhov length must be nonzero')
        self.ustar,self.L,self.z0=float(ustar),float(obukhov),float(z0)
        unstable_factor=(12-.5*boundary_layer_m/self.L)**(1/3) if self.L<0 else 2.5
        self.sigma_u=float(sigma_u) if sigma_u else unstable_factor*self.ustar
        self.sigma_v=float(sigma_v) if sigma_v else .8*self.sigma_u
        self.uw=-self.ustar**2
    def zeta(self,z):return 0.*z if math.isinf(self.L) else z/self.L
    def wind(self,z):
        return self.ustar/KARMAN*(np.log(z/self.z0)-psi_m(self.zeta(z))+psi_m(self.zeta(self.z0+0.*z)))
    def sigma_w2(self,z):
        factor=np.power(np.maximum(1-3*np.minimum(self.zeta(z),0),1),1/3)
        return (1.25*self.ustar*factor)**2
    def dsigma_w2(self,z):
        if self.L>=0:return 0.*z
        h=1e-3*np.maximum(z,.01)
        return (self.sigma_w2(z+h)-self.sigma_w2(z-h))/(2*h)
    def epsilon(self,z):
        zeta=self.zeta(z)
        return self.ustar**3/(KARMAN*z)*np.maximum(phi_m(zeta)-zeta,.1)


def ustar_from_wind(speed,height,z0,obukhov):
    """u* that reproduces the measured mean speed at height under the MOST profile."""
    zeta=0. if math.isinf(obukhov) else height/obukhov
    zeta0=0. if math.isinf(obukhov) else z0/obukhov
    denominator=math.log(height/z0)-float(psi_m(zeta))+float(psi_m(zeta0))
    if denominator<=0:raise ValueError('Profile undefined for this stability')
    return KARMAN*speed/denominator


class Particles:
    """Positions and velocity fluctuations (u' along-wind, v', w') for a particle cloud."""
    def __init__(self,layer,x,y,z,rng):
        n=len(z);self.x,self.y,self.z=np.asarray(x,float).copy(),np.asarray(y,float).copy(),np.asarray(z,float).copy()
        s2w=layer.sigma_w2(self.z)
        self.w=rng.standard_normal(n)*np.sqrt(s2w)
        self.u=layer.uw/s2w*self.w+rng.standard_normal(n)*np.sqrt(np.maximum(layer.sigma_u**2-layer.uw**2/s2w,1e-12))
        self.v=rng.standard_normal(n)*layer.sigma_v
    def keep(self,mask):
        for name in ('x','y','z','u','v','w'):setattr(self,name,getattr(self,name)[mask])


def step(layer,p,rng,backward,reflect_height,top=None):
    """Advance one Thomson (1987) step; backward=True reverses time (Flesch et al. 1995). Returns dt per particle."""
    s2u,s2v=layer.sigma_u**2,layer.sigma_v**2
    s2w=layer.sigma_w2(p.z);ds2w=layer.dsigma_w2(p.z);eps=layer.epsilon(p.z)
    det=s2u*s2w-layer.uw**2
    l13,l33=-layer.uw/det,s2u/det;l11=s2w/det
    dt=np.clip(.025*2*s2w/(C0*eps),1e-3,.5)
    coefficient=C0*eps
    # The mean-shear term cancels in fluctuation form; the sigma_w gradient term flips sign in reverse time.
    sign=-1. if backward else 1.
    au=-.5*coefficient*(l11*p.u+l13*p.w)
    aw=-.5*coefficient*(l13*p.u+l33*p.w)+sign*.5*ds2w*(1+(l13*p.u+l33*p.w)*p.w)
    av=-.5*coefficient*p.v/s2v
    noise=np.sqrt(coefficient*dt)
    n=len(p.z)
    u=p.u+au*dt+noise*rng.standard_normal(n)
    w=p.w+aw*dt+noise*rng.standard_normal(n)
    v=p.v+av*dt+noise*rng.standard_normal(n)
    p.x=p.x+sign*(layer.wind(p.z)+p.u)*dt
    p.y=p.y+sign*p.v*dt
    z=p.z+sign*p.w*dt
    for boundary,below in ((reflect_height,z<reflect_height),(top,None if top is None else z>top)):
        if below is None or not below.any():continue
        # Perfect reflection preserving the joint (u',w') Gaussian: u' -> u' - 2(uw/sw2) w, w -> -w.
        z[below]=2*boundary-z[below]
        u[below]=u[below]-2*layer.uw/layer.sigma_w2(z[below])*w[below]
        w[below]=-w[below]
    p.u,p.v,p.w,p.z=u,v,w,z
    return dt


def _box_residence(p,dt,boxes):
    """boxes: array of (x, y, z_lo, z_hi, half_width). Returns residence time per box for this step."""
    out=np.zeros(len(boxes))
    for k,(bx,by,lo,hi,half) in enumerate(boxes):
        inside=(np.abs(p.x-bx)<=half)&(np.abs(p.y-by)<=half)&(p.z>=lo)&(p.z<=hi)
        if inside.any():out[k]=dt[inside].sum()
    return out


def _boxes(points,reflect_height,half_width,half_depth):
    points=np.asarray(points,float).reshape(-1,3)
    lo=np.maximum(points[:,2]-half_depth,reflect_height);hi=np.maximum(points[:,2]+half_depth,lo+2*half_depth)
    boxes=np.column_stack([points[:,0],points[:,1],lo,hi,np.full(len(points),half_width)])
    return boxes,(2*half_width)**2*(hi-lo)


def unit_response(layer,receptor_height,sources,particles=50000,seed=0,box_half_width=.5,slab_half_depth=.25,
                  reflect_height=None,max_steps=200000,top=300.):
    """(C/Q) in s/m^3 for each source given as (x<0 upwind, y crosswind, height) relative to the receptor.

    Particles start at the receptor and run backward in time; residence time inside a small box around each source,
    divided by box volume and particle count, is the concentration per unit emission (Flesch et al. 1995).
    """
    zr=layer.z0 if reflect_height is None else float(reflect_height)
    if receptor_height<=zr:raise ValueError('Receptor below reflection height')
    rng=np.random.default_rng(seed)
    n=int(particles)
    boxes,volume=_boxes(sources,zr,box_half_width,slab_half_depth)
    p=Particles(layer,np.zeros(n),np.zeros(n),np.full(n,float(receptor_height)),rng)
    residence=np.zeros(len(boxes))
    x_stop=boxes[:,0].min()-box_half_width-1.
    for _ in range(max_steps):
        if not len(p.z):break
        x,y,z=p.x,p.y,p.z
        dt=step(layer,p,rng,True,zr)
        # Score the start-of-step position (left Riemann sum), then drop particles beyond every source.
        start=Particles.__new__(Particles);start.x,start.y,start.z=x,y,z
        residence+=_box_residence(start,dt,boxes)
        p.keep((p.x>=x_stop)&(p.z<top))
    if len(p.z):raise RuntimeError('Particles did not leave the domain; increase max_steps')
    return residence/(volume*n)


def forward_response(layer,source,receptors,particles=50000,seed=0,box_half_width=.5,slab_half_depth=.25,
                     reflect_height=None,max_steps=200000,top=300.):
    """Forward-time (C/Q) at receptor boxes downwind of one source; used only to verify the backward model."""
    zr=layer.z0 if reflect_height is None else float(reflect_height)
    rng=np.random.default_rng(seed);n=int(particles)
    boxes,volume=_boxes(receptors,zr,box_half_width,slab_half_depth)
    p=Particles(layer,np.full(n,float(source[0])),np.full(n,float(source[1])),np.full(n,float(source[2])),rng)
    residence=np.zeros(len(boxes));x_stop=boxes[:,0].max()+box_half_width+1.
    for _ in range(max_steps):
        if not len(p.z):break
        x,y,z=p.x,p.y,p.z
        dt=step(layer,p,rng,False,zr)
        start=Particles.__new__(Particles);start.x,start.y,start.z=x,y,z
        residence+=_box_residence(start,dt,boxes)
        p.keep((p.x<=x_stop)&(p.z<top))
    if len(p.z):raise RuntimeError('Particles did not leave the domain; increase max_steps')
    return residence/(volume*n)


def ppm_per_kg_h(concentration_per_emission,temperature_k,pressure_pa):
    """Convert s/m^3 to methane ppm per kg/h."""
    return np.asarray(concentration_per_emission)/3600*8.314462618*temperature_k/(pressure_pa*.01604)*1e6


def wind_frame(source_enu,receptor_enu,wind_from_degrees):
    """Source position relative to receptor in a wind-aligned frame: x downwind, y crosswind, z height."""
    east,north=source_enu[0]-receptor_enu[0],source_enu[1]-receptor_enu[1]
    theta=math.radians(wind_from_degrees)
    along=-east*math.sin(theta)-north*math.cos(theta)
    cross=east*math.cos(theta)-north*math.sin(theta)
    return along,cross,source_enu[2]
