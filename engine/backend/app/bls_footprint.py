"""Receptor footprint from one backward bLs run, for many sources and wind directions at once.

The backward cloud from a receptor depends only on the surface layer and the receptor height, not on where the
sources are. Residence time is binned on a wind-aligned (x upwind negative, y crosswind) grid for each source
height slab, so the response to any source at any wind direction is a grid lookup. Same physics and units as
app.bls.unit_response, which this reproduces at bin centres.
"""
import math
import numpy as np
from app.bls import Particles,step

METHOD='backward-lagrangian-stochastic-footprint-v0'


class Footprint:
    """C/Q in s/m^3 on grid cells (1 m by default) for each source height slab."""
    def __init__(self,grid,x_edges,y_edges,heights):
        self.grid,self.x_edges,self.y_edges,self.heights=grid,x_edges,y_edges,np.asarray(heights,float)

    def response(self,along,cross,height):
        """Bilinear lookup at bin centres; zero outside the grid or downwind of the receptor."""
        k=int(np.argmin(np.abs(self.heights-height)))
        if abs(self.heights[k]-height)>1e-6:raise ValueError(f'No slab for source height {height}')
        cx=(self.x_edges[:-1]+self.x_edges[1:])/2;cy=(self.y_edges[:-1]+self.y_edges[1:])/2
        if not(cx[0]<=along<=cx[-1] and cy[0]<=cross<=cy[-1]):return 0.
        i=min(int(np.searchsorted(cx,along))-1,len(cx)-2);j=min(int(np.searchsorted(cy,cross))-1,len(cy)-2)
        i,j=max(i,0),max(j,0)
        fx=(along-cx[i])/(cx[i+1]-cx[i]);fy=(cross-cy[j])/(cy[j+1]-cy[j])
        g=self.grid[k]
        return float((1-fx)*(1-fy)*g[i,j]+fx*(1-fy)*g[i+1,j]+(1-fx)*fy*g[i,j+1]+fx*fy*g[i+1,j+1])


def footprint(layer,receptor_height,source_heights,reach=150.,cell=1.,particles=100000,seed=0,slab_half_depth=.25,
              reflect_height=None,max_steps=200000,top=300.):
    zr=layer.z0 if reflect_height is None else float(reflect_height)
    if receptor_height<=zr:raise ValueError('Receptor below reflection height')
    heights=sorted({float(h) for h in source_heights})
    lo=np.array([max(h-slab_half_depth,zr) for h in heights]);hi=np.maximum(np.array(heights)+slab_half_depth,lo+2*slab_half_depth)
    x_edges=np.arange(-reach-cell/2,cell/2+1e-9,cell);y_edges=np.arange(-reach-cell/2,reach+cell/2+1e-9,cell)
    nx,ny=len(x_edges)-1,len(y_edges)-1
    rng=np.random.default_rng(seed);n=int(particles)
    p=Particles(layer,np.zeros(n),np.zeros(n),np.full(n,float(receptor_height)),rng)
    flat=np.zeros((len(heights),nx*ny))
    for _ in range(max_steps):
        if not len(p.z):break
        x,y,z=p.x,p.y,p.z
        dt=step(layer,p,rng,True,zr)
        # Left Riemann sum on the start-of-step position, as in app.bls.unit_response.
        i=np.floor((x-x_edges[0])/cell).astype(np.int64);j=np.floor((y-y_edges[0])/cell).astype(np.int64)
        inside=(i>=0)&(i<nx)&(j>=0)&(j<ny)
        for k in range(len(heights)):
            m=inside&(z>=lo[k])&(z<=hi[k])
            if m.any():flat[k]+=np.bincount(i[m]*ny+j[m],weights=dt[m],minlength=nx*ny)
        p.keep((p.x>=x_edges[0])&(p.z<top))
    if len(p.z):raise RuntimeError('Particles did not leave the domain; increase max_steps')
    volume=(cell*cell*(hi-lo))[:,None]
    return Footprint((flat/(volume*n)).reshape(len(heights),nx,ny),x_edges,y_edges,heights)
