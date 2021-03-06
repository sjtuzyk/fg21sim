# Copyright (c) 2017-2018 Weitian LI <weitian@aaronly.me>
# MIT license

"""
Simulate (giant) radio halos originating from the recent merger
events, which generate cluster-wide turbulence and accelerate the
primary (i.e., fossil) relativistic electrons to high energies to
be synchrotron-bright.  This *turbulence re-acceleration* model
is currently most widely accepted to explain the (giant) radio halos.

The simulation method is somewhat based on the statistical (Monte
Carlo) method proposed by [cassano2005]_, but with extensive
modifications and improvements.

References
----------
.. [brunetti2011]
   Brunetti & Lazarian 2011, MNRAS, 410, 127
   http://adsabs.harvard.edu/abs/2011MNRAS.410..127B

.. [cassano2005]
   Cassano & Brunetti 2005, MNRAS, 357, 1313
   http://adsabs.harvard.edu/abs/2005MNRAS.357.1313C

.. [cassano2006]
   Cassano, Brunetti & Setti, 2006, MNRAS, 369, 1577
   http://adsabs.harvard.edu/abs/2006MNRAS.369.1577C

.. [cassano2012]
   Cassano et al. 2012, A&A, 548, A100
   http://adsabs.harvard.edu/abs/2012A%26A...548A.100C

.. [donnert2013]
   Donnert 2013, AN, 334, 615
   http://adsabs.harvard.edu/abs/2013AN....334..515D

.. [donnert2014]
   Donnert & Brunetti 2014, MNRAS, 443, 3564
   http://adsabs.harvard.edu/abs/2014MNRAS.443.3564D

.. [hogg1999]
   Hogg 1999, arXiv:astro-ph/9905116
   http://adsabs.harvard.edu/abs/1999astro.ph..5116H

.. [miniati2015]
   Miniati 2015, ApJ, 800, 60
   http://adsabs.harvard.edu/abs/2015ApJ...800...60M

.. [pinzke2017]
   Pinzke, Oh & Pfrommer 2017, MNRAS, 465, 4800
   http://adsabs.harvard.edu/abs/2017MNRAS.465.4800P

.. [sarazin1999]
   Sarazin 1999, ApJ, 520, 529
   http://adsabs.harvard.edu/abs/1999ApJ...520..529S
"""

import logging
from functools import lru_cache

import numpy as np

from . import helper
from .solver import FokkerPlanckSolver
from ...share import CONFIGS, COSMO
from ...utils.units import (Units as AU,
                            UnitConversions as AUC,
                            Constants as AC)


logger = logging.getLogger(__name__)


class RadioHalo:
    """
    Simulate the radio halo properties for a galaxy cluster that is
    experiencing an on-going merger or had a merger recently.

    Description
    -----------
    1. Calculate the turbulence persistence time (tau_turb; ~<1 Gyr);
    2. Calculate the diffusion coefficient (D_pp) from the systematic
       acceleration timescale (tau_acc; ~0.1 Gyr).  The acceleration
       diffusion is assumed to have an action time ~ tau_turb (i.e.,
       only during turbulence persistence), and then is disabled (i.e.,
       only radiation and ionization losses later);
    3. Assume the electrons are constantly injected and has a power-law
       energy spectrum, determine the injection rate by further assuming
       that the total injected electrons has energy of a fraction (eta_e)
       of the ICM total thermal energy;
    4. Set the electron density/spectrum be the accumulated electrons
       injected during t_merger time, then evolve it for time_init period
       considering only losses and constant injection, in order to derive
       an approximately steady electron spectrum for following use;
    5. Calculate the magnetic field from the cluster total mass (which
       is assumed to be growth linearly from M_main to M_obs);
    6. Calculate the energy losses for the coefficients of Fokker-Planck
       equation;
    7. Solve the Fokker-Planck equation to derive the relativistic
       electron spectrum at t_obs (i.e., z_obs);
    8. Calculate the synchrotron emissivity from the derived electron
       spectrum.

    Parameters
    ----------
    M_obs : float
        Cluster virial mass at the current observation (simulation end) time.
        Unit: [Msun]
    z_obs : float
        Redshift of the current observation (simulation end) time.
    M_main, M_sub : float
        The main and sub cluster masses before the (major) merger.
        Unit: [Msun]
    z_merger : float
        The redshift when the (major) merger begins.

    Attributes
    ----------
    fpsolver : `~FokkerPlanckSolver`
        The solver instance to calculate the electron spectrum evolution.
    radius : float
        The halo radius
        Unit: [kpc]
    gamma : 1D float `~numpy.ndarray`
        The Lorentz factors of the adopted logarithmic grid to solve the
        equation.
    electron_spec : 1D float `~numpy.ndarray`
        The derived electron (number density) distribution/spectrum at
        the final time (``zend``), which is set by the methods
        ``self.calc_electron_spectrum()`` or ``self.set_electron_spectrum()``.
        Unit: [cm^-3]
    """
    # Component name
    compID = "extragalactic/halos"
    name = "giant radio halos"

    def __init__(self, M_obs, z_obs, M_main, M_sub, z_merger,
                 configs=CONFIGS):
        self.M_obs = M_obs
        self.z_obs = z_obs
        self.age_obs = COSMO.age(z_obs)
        self.M_main = M_main
        self.M_sub = M_sub
        self.z_merger = z_merger
        self.age_merger = COSMO.age(z_merger)

        self._set_configs(configs)
        self._set_solver()

    def _set_configs(self, configs):
        comp = self.compID
        self.configs = configs
        self.f_acc = configs.getn(comp+"/f_acc")
        self.f_lturb = configs.getn(comp+"/f_lturb")
        self.zeta_ins = configs.getn(comp+"/zeta_ins")
        self.eta_turb = configs.getn(comp+"/eta_turb")
        self.eta_e = configs.getn(comp+"/eta_e")
        self.x_cr = configs.getn(comp+"/x_cr")
        self.gamma_min = configs.getn(comp+"/gamma_min")
        self.gamma_max = configs.getn(comp+"/gamma_max")
        self.gamma_np = configs.getn(comp+"/gamma_np")
        self.buffer_np = configs.getn(comp+"/buffer_np")
        if self.buffer_np == 0:
            self.buffer_np = None
        self.time_step = configs.getn(comp+"/time_step")
        self.time_init = configs.getn(comp+"/time_init")
        self.injection_index = configs.getn(comp+"/injection_index")

    def _set_solver(self):
        self.fpsolver = FokkerPlanckSolver(
            xmin=self.gamma_min,
            xmax=self.gamma_max,
            x_np=self.gamma_np,
            tstep=self.time_step,
            f_advection=self.fp_advection,
            f_diffusion=self.fp_diffusion,
            f_injection=self.fp_injection,
            buffer_np=self.buffer_np,
        )

    @property
    @lru_cache()
    def gamma(self):
        """
        The logarithmic grid adopted for solving the equation.
        """
        return self.fpsolver.x

    @property
    def age_begin(self):
        """
        The cosmic time when the merger begins.
        Unit: [Gyr]
        """
        return self.age_merger

    def time_turbulence(self, t=None):
        """
        The duration that the merger-induced turbulence persists, which
        is used to approximate the lasting time of the effective turbulence
        acceleration.

        Unit: [Gyr]
        """
        t_merger = self._merger_time(t)
        mass_main = self.mass_main(t=t_merger)
        mass_sub = self.mass_sub(t=t_merger)
        z_merger = COSMO.redshift(t_merger)
        return helper.time_turbulence(mass_main, mass_sub, z=z_merger,
                                      configs=self.configs)

    def mach_turbulence(self, t=None):
        """
        The turbulence Mach number determined from its velocity dispersion.
        """
        t_merger = self._merger_time(t)
        cs = helper.speed_sound(self.kT(t_merger))  # [km/s]
        v_turb = self._velocity_turb(t_merger)  # [km/s]
        return v_turb / cs

    @property
    def radius_virial_obs(self):
        """
        The virial radius of the "current" cluster (``M_obs``) at
        ``z_obs``.

        Unit: [kpc]
        """
        return helper.radius_virial(mass=self.M_obs, z=self.z_obs)

    @property
    def radius(self):
        """
        The estimated radius for the simulated radio halo.
        Unit: [kpc]
        """
        return self.injection_radius

    @property
    def angular_radius(self):
        """
        The angular radius of the radio halo.
        Unit: [arcsec]
        """
        DA = COSMO.DA(self.z_obs) * 1e3  # [Mpc] -> [kpc]
        theta = self.radius / DA  # [rad]
        return theta * AUC.rad2arcsec

    @property
    def volume(self):
        """
        The halo volume, calculated from the above radius.
        Unit: [kpc^3]
        """
        return (4*np.pi/3) * self.radius**3

    @property
    def B_obs(self):
        """
        The magnetic field strength at the simulated observation
        time (i.e., cluster mass of ``self.M_obs``), will be used
        to calculate the synchrotron emissions.

        Unit: [uG]
        """
        return helper.magnetic_field(mass=self.M_obs, z=self.z_obs,
                                     configs=self.configs)

    @property
    def kT_obs(self):
        """
        The ICM mean temperature of the cluster at ``z_obs``.
        Unit: [keV]
        """
        return helper.kT_cluster(self.M_obs, z=self.z_obs,
                                 configs=self.configs)

    def kT(self, t=None):
        """
        The ICM mean temperature of the main cluster at cosmic time
        ``t`` (default: ``self.age_begin``).

        Unit: [keV]
        """
        if t is None:
            t = self.age_begin
        mass = self.mass_main(t)
        z = COSMO.redshift(t)
        return helper.kT_cluster(mass=mass, z=z, configs=self.configs)

    def tau_acceleration(self, t):
        """
        Calculate the electron acceleration timescale due to turbulent
        waves, which describes the turbulent acceleration efficiency.
        The turbulent acceleration timescale has order of ~0.1 Gyr.

        Here we consider the turbulence cascade mode through scattering
        in the high-β ICM mediated by plasma instabilities (firehose,
        mirror) rather than Coulomb scattering.  Therefore, the fast modes
        damp by TTD (transit time damping) on relativistic rather than
        thermal particles, and the diffusion coefficient is given by:
            D_pp = (4π * p^2 * ζ / X_cr / L_turb) * <v_turb^2>^2 / c_s^3
        where:
            ζ: efficiency factor for the effectiveness of plasma instabilities
            X_cr: relative energy density of cosmic rays
            L_turb: turbulence injection scale
            v_turb: turbulence velocity dispersion
            c_s: sound speed
        Thus the acceleration timescale is:
            τ_acc = p^2 / (4*D_pp)
                  = (X_cr * c_s^3 * L_turb) / (16π * ζ * <v_turb^2>^2)

        WARNING
        -------
        Current test shows that a very large acceleration timescale (e.g.,
        1000 or even larger) will cause problems (maybe due to some
        limitations within the current calculation scheme), for example,
        the energy losses don't seem to have effect in such cases, so the
        derived initial electron spectrum is almost the same as the raw
        input one, and the emissivity at medium/high frequencies even
        decreases when the turbulence acceleration begins!
        By carrying out some tests, the value of 10 [Gyr] is adopted for
        the maximum acceleration timescale.

        Parameters
        ----------
        t : float, optional
            The cosmic time when to determine the acceleration timescale.
            Unit: [Gyr]

        Returns
        -------
        tau : float
            The acceleration timescale at the requested time.
            Unit: [Gyr]

        References
        ----------
        * Ref.[pinzke2017],Eq.(37)
        * Ref.[miniati2015],Eq.(29)
        """
        # Maximum acceleration timescale when no turbulence acceleration
        # NOTE: see the above WARNING!
        tau_max = 10.0  # [Gyr]
        if not self._is_turb_active(t):
            return tau_max

        t_merger = self._merger_time(t)
        z_merger = COSMO.redshift(t_merger)
        mass_merged = self.mass_merged(t_merger)
        R_vir = helper.radius_virial(mass=mass_merged, z=z_merger)
        L = self.f_lturb * R_vir  # [kpc]
        cs = helper.speed_sound(self.kT(t_merger))  # [km/s]
        v_turb = self._velocity_turb(t_merger)  # [km/s]
        tau = (self.x_cr * cs**3 * L /
               (16*np.pi * self.zeta_ins * v_turb**4))  # [s kpc/km]
        tau *= AUC.s2Gyr * AUC.kpc2km  # [Gyr]
        tau *= self.f_acc  # custom tune parameter

        # Impose the maximum acceleration timescale
        if tau > tau_max:
            tau = tau_max
        return tau

    @property
    @lru_cache
    def injection_radius(self):
        """
        The radius of the turbulence injection regions, and then the
        injection scale: L_turb ~= 2*R_turb.
        Unit: [kpc]
        """
        rs = helper.radius_stripping(self.M_main, self.M_sub, self.z_merger,
                                     configs=self.configs)  # [kpc]
        return self.f_lturb * rs

    @property
    @lru_cache()
    def injection_rate(self):
        """
        The constant electron injection rate assumed.
        Unit: [cm^-3 Gyr^-1]

        The injection rate is parametrized by assuming that the total
        energy injected in the relativistic electrons during the cluster
        life (e.g., ``age_obs`` here) is a fraction (``self.eta_e``)
        of the total thermal energy of the cluster.

        The electrons are assumed to be injected throughout the cluster
        ICM/volume, i.e., do not restricted inside the halo volume.

        Qe(γ) = Ke * γ^(-s),
        int[ Qe(γ) γ me c^2 ]dγ * t_cluster = η_e * e_th
        =>
        Ke = [(s-2) * η_e * e_th * γ_min^(s-2) / (me * c^2 * t_cluster)]

        References
        ----------
        Ref.[cassano2005],Eqs.(31,32,33)
        """
        s = self.injection_index
        e_th = helper.density_energy_thermal(self.M_obs, self.z_obs,
                                             configs=self.configs)
        term1 = (s-2) * self.eta_e * e_th  # [erg cm^-3]
        term2 = self.gamma_min**(s-2)
        term3 = AU.mec2 * self.age_obs  # [erg Gyr]
        Ke = term1 * term2 / term3  # [cm^-3 Gyr^-1]
        return Ke

    @property
    def electron_spec_init(self):
        """
        The electron spectrum at ``age_begin`` to be used as the initial
        condition for the Fokker-Planck equation.

        This initial electron spectrum is derived from the accumulated
        electron spectrum injected throughout the ``age_begin`` period,
        by solving the same Fokker-Planck equation, but only considering
        energy losses and constant injection, evolving for a period of
        ``time_init`` in order to obtain an approximately steady electron
        spectrum.

        Units: [cm^-3]
        """
        # Accumulated electrons constantly injected until ``age_begin``
        n_inj = self.fp_injection(self.gamma)
        n0_e = n_inj * (self.age_begin - self.time_init)

        logger.debug("Derive the initial electron spectrum ...")
        dt = self.time_step
        tstart = self.age_begin - self.time_init - dt
        tstop = self.age_begin - dt  # avoid acceleration at the ``age_begin``
        # Use a bigger time step to save time
        self.fpsolver.tstep = 3 * dt
        n_e = self.fpsolver.solve(u0=n0_e, tstart=tstart, tstop=tstop)
        # Restore the original time step
        self.fpsolver.tstep = dt
        return n_e

    def calc_electron_spectrum(self, tstart=None, tstop=None, n0_e=None):
        """
        Calculate the relativistic electron spectrum by solving the
        Fokker-Planck equation.

        Parameters
        ----------
        tstart : float, optional
            The (cosmic) time from when to solve the Fokker-Planck equation
            for relativistic electrons evolution.
            Default: ``self.age_begin``.
            Unit: [Gyr]
        tstop : float, optional
            The (cosmic) time when to derive final relativistic electrons
            spectrum for synchrotron emission calculations.
            Default: ``self.age_obs``.
            Unit: [Gyr]
        n0_e : 1D `~numpy.ndarray`, optional
            The initial electron spectrum (number distribution).
            Default: ``self.electron_spec_init``
            Unit: [cm^-3]

        Returns
        -------
        electron_spec : float 1D `~numpy.ndarray`
            The solved electron spectrum at ``tstop``.
            Unit: [cm^-3]
        """
        if tstart is None:
            tstart = self.age_begin
        if tstop is None:
            tstop = self.age_obs
        if n0_e is None:
            n0_e = self.electron_spec_init

        # Decrease the time step when the evolution time is short.
        # (necessary?)
        nstep_min = 20
        if tstop - tstart < self.time_step * nstep_min:
            self.fpsolver.tstep = (tstop - tstart) / nstep_min
            logger.debug("Decreased time step: %g -> %g [Gyr]" %
                         (self.time_step, self.fpsolver.tstep))

        self.electron_spec = self.fpsolver.solve(u0=n0_e, tstart=tstart,
                                                 tstop=tstop)
        return self.electron_spec

    def set_electron_spectrum(self, n_e):
        """
        Check the given electron spectrum and set it to the
        ``self.electron_spec``.

        Parameters
        ----------
        n_e : float 1D `~numpy.ndarray`
            The solved electron spectrum at ``zend``.
            Unit: [cm^-3]
        """
        n_e = np.array(n_e)  # make a copy
        if n_e.shape == self.gamma.shape:
            self.electron_spec = n_e
        else:
            raise ValueError("given electron spectrum has wrong shape!")

    def fp_injection(self, gamma, t=None):
        """
        Electron injection (rate) term for the Fokker-Planck equation.

        NOTE
        ----
        The injected electrons are assumed to have a power-law spectrum
        and a constant injection rate.

        Qe(γ) = Ke * γ^(-s),
        Ke: constant injection rate

        Parameters
        ----------
        gamma : float, or float 1D `~numpy.ndarray`
            Lorentz factors of electrons
        t : None
            Currently a constant injection rate is assumed, therefore
            this parameter is not used.  Keep it for the consistency
            with other functions.

        Returns
        -------
        Qe : float, or float 1D `~numpy.ndarray`
            Current electron injection rate at specified energies (gamma).
            Unit: [cm^-3 Gyr^-1]

        References
        ----------
        Ref.[cassano2005],Eqs.(31,32,33)
        """
        Ke = self.injection_rate  # [cm^-3 Gyr^-1]
        Qe = Ke * gamma**(-self.injection_index)
        return Qe

    def fp_diffusion(self, gamma, t):
        """
        Diffusion term/coefficient for the Fokker-Planck equation.

        The diffusion is directly related to the electron acceleration
        which is described by the ``tau_acc`` acceleration timescale
        parameter.

        WARNING
        -------
        A zero diffusion coefficient may lead to unstable/wrong results,
        since it is not properly taken care of by the solver.

        Parameters
        ----------
        gamma : float, or float 1D `~numpy.ndarray`
            The Lorentz factors of electrons
        t : float
            Current (cosmic) time when solving the equation
            Unit: [Gyr]

        Returns
        -------
        diffusion : float, or float 1D `~numpy.ndarray`
            Diffusion coefficients
            Unit: [Gyr^-1]

        References
        ----------
        Ref.[donnert2013],Eq.(15)
        """
        tau_acc = self.tau_acceleration(t)
        gamma = np.asarray(gamma)
        diffusion = gamma**2 / 4 / tau_acc
        return diffusion

    def fp_advection(self, gamma, t):
        """
        Advection term/coefficient for the Fokker-Planck equation,
        which describes a systematic tendency for upward or downward
        drift of particles.

        This term is also called the "generalized cooling function"
        by [donnert2014], which includes all relevant energy loss
        functions and the energy gain function due to turbulence.

        Returns
        -------
        advection : float, or float 1D `~numpy.ndarray`
            Advection coefficients, describing the energy loss/gain rates.
            Unit: [Gyr^-1]
        """
        if t < self.age_begin:
            # To derive the initial electron spectrum
            advection = abs(self._energy_loss(gamma, self.age_begin))
        else:
            # Turbulence acceleration and beyond
            advection = (abs(self._energy_loss(gamma, t)) -
                         (self.fp_diffusion(gamma, t) * 2 / gamma))
        return advection

    def _merger_time(self, t=None):
        """
        The (cosmic) time when the merger begins.
        Unit: [Gyr]
        """
        return self.age_merger

    def mass_merged(self, t=None):
        """
        The mass of the merged cluster.
        Unit: [Msun]
        """
        return self.M_main + self.M_sub

    def mass_sub(self, t=None):
        """
        The mass of the sub cluster.
        Unit: [Msun]
        """
        return self.M_sub

    def mass_main(self, t):
        """
        Calculate the main cluster mass at the given (cosmic) time.

        NOTE
        ----
        Since we currently only consider the last major merger event,
        there may be a long time between ``z_merger`` and ``z_obs``.
        So we assume that the main cluster grows linearly in time from
        (M_main, z_merger) to (M_obs, z_obs).

        Parameters
        ----------
        t : float
            The (cosmic) time/age.
            Unit: [Gyr]

        Returns
        -------
        mass : float
            The mass of the main cluster.
            Unit: [Msun]
        """
        t0 = self.age_begin
        rate = (self.M_obs - self.M_main) / (self.age_obs - t0)
        mass = rate * (t - t0) + self.M_main
        return mass

    def magnetic_field(self, t):
        """
        Calculate the mean magnetic field strength of the main cluster mass
        at the given (cosmic) time.

        Returns
        -------
        B : float
            The mean magnetic field strength of the main cluster.
            Unit: [uG]
        """
        z = COSMO.redshift(t)
        mass = self.mass_main(t)  # [Msun]
        B = helper.magnetic_field(mass=mass, z=z, configs=self.configs)
        return B

    def _velocity_turb(self, t):
        """
        Calculate the turbulence velocity dispersion (i.e., turbulence
        Mach number).

        NOTE
        ----
        During the merger, a fraction of the merger kinetic energy is
        transferred into the turbulence within the assumed regions
        (radius <= L, the injection scale).  Then estimate the turbulence
        velocity dispersion from its energy.

        Merger energy:
            E_m ≅ 0.5 * f_gas * M_sub * v_vir^2
            v_vir = sqrt(G*M_main / R_vir)
        Turbulence energy:
            E_turb ≅ η_turb * E_m
                   ≅ 0.5 * M_turb * <v_turb^2>
                   = 0.5 * f_gas * M_total(<L) * <v_turb^2>
                   = 0.5 * f_gas * f_mass(L/R_vir) * M_total * <v_turb^2>
            M_total = M_main + M_sub
        => Velocity dispersion:
            <v_turb^2> ≅ (η_turb/f_mass) * (M_sub/M_total) * v_vir^2

        Returns
        -------
        v_turb : float
            The turbulence velocity dispersion
            Unit: [km/s]
        """
        z = COSMO.redshift(t)
        mass_merged = self.mass_merged(t)
        mass_main = self.mass_main(t)
        mass_sub = self.mass_sub(t)
        R_vir = helper.radius_virial(mass_merged, z) * AUC.kpc2cm  # [cm]
        v2_vir = (AC.G * mass_main*AUC.Msun2g / R_vir) * AUC.cm2km**2
        fmass = helper.fmass_nfw(self.f_lturb)
        v2_turb = v2_vir * (self.eta_turb / fmass) * (mass_sub / mass_merged)
        return np.sqrt(v2_turb)

    def _is_turb_active(self, t):
        """
        Is the turbulence acceleration is active at the given (cosmic) time?

        NOTE
        ----
        Considering that the turbulence acceleration is a 2nd-order Fermi
        process, it has only an effective acceleration time ~<1 Gyr.
        Therefore, only during the period that strong turbulence persists
        in the ICM that the turbulence could effectively accelerate the
        relativistic electrons.
        """
        if t < self.age_begin:
            return False
        t_merger = self._merger_time(t)
        t_turb = self.time_turbulence(t_merger)
        return (t >= t_merger) and (t <= t_merger + t_turb)

    def _energy_loss(self, gamma, t):
        """
        Energy loss mechanisms:
        * inverse Compton scattering off the CMB photons
        * synchrotron radiation
        * Coulomb collisions

        Reference: Ref.[sarazin1999],Eqs.(6,7,9)

        Parameters
        ----------
        gamma : float, or float 1D `~numpy.ndarray`
            The Lorentz factors of electrons
        t : float
            The cosmic time/age
            Unit: [Gyr]

        Returns
        -------
        loss : float, or float 1D `~numpy.ndarray`
            The energy loss rates
            Unit: [Gyr^-1]
        """
        gamma = np.asarray(gamma)
        z = COSMO.redshift(t)
        B = self.magnetic_field(t)  # [uG]
        mass = self.mass_main(t)
        n_th = helper.density_number_thermal(mass, z)  # [cm^-3]
        loss_ic = -4.32e-4 * gamma**2 * (1+z)**4
        loss_syn = -4.10e-5 * gamma**2 * B**2
        loss_coul = -3.79e4 * n_th * (1 + np.log(gamma/n_th) / 75)
        return loss_ic + loss_syn + loss_coul


class RadioHaloAM(RadioHalo):
    """
    Simulate the radio halo properties for a galaxy cluster with all its
    on-going merger and past merger events taken into account.

    Parameters
    ----------
    M_obs : float
        Cluster virial mass at the observation (simulation end) time.
        Unit: [Msun]
    z_obs : float
        Redshift of the observation (simulation end) time.
    M_main, M_sub : list[float]
        List of main and sub cluster masses at each merger event,
        from current to earlier time.
        Unit: [Msun]
    z_merger : list[float]
        The redshifts at each merger event, from small to large.
    merger_num : int
        Number of merger events traced for the cluster.
    """
    def __init__(self, M_obs, z_obs, M_main, M_sub, z_merger,
                 merger_num, configs=CONFIGS):
        self.merger_num = merger_num
        M_main = np.asarray(M_main[:merger_num])
        M_sub = np.asarray(M_sub[:merger_num])
        z_merger = np.asarray(z_merger[:merger_num])
        super().__init__(M_obs=M_obs, z_obs=z_obs,
                         M_main=M_main, M_sub=M_sub,
                         z_merger=z_merger, configs=configs)

    @property
    def age_begin(self):
        """
        The cosmic time when the merger begins, i.e., the earliest merger.
        Unit: [Gyr]
        """
        return self.age_merger[-1]

    def _merger_idx(self, t):
        """
        Determine the index of the merger event within which the given
        time is located, i.e.:
            age_merger[idx-1] >= t > age_merger[idx]
        """
        return (self.age_merger > t).sum()

    def _merger_time(self, t):
        """
        Determine the beginning time of the merger event within which
        the given time is located.
        """
        idx = self._merger_idx(t)
        return self.age_merger[idx]

    def _merger(self, t):
        """
        Return the merger event at cosmic time ``t``.
        """
        idx = self._merger_idx(t)
        return {
            "idx": idx,
            "M_main": self.M_main[idx],
            "M_sub": self.M_sub[idx],
            "z": self.z_merger[idx],
            "age": self.age_merger[idx],
        }

    def mass_merged(self, t):
        """
        The mass of merged cluster at the given (cosmic) time.
        Unit: [Msun]
        """
        if t >= self.age_obs:
            return self.M_obs
        else:
            merger = self._merger(t)
            return (merger["M_main"] + merger["M_sub"])

    def mass_sub(self, t):
        """
        The mass of the sub cluster at the given (cosmic) time.
        Unit: [Msun]
        """
        merger = self._merger(t)
        return merger["M_sub"]

    def mass_main(self, t):
        """
        Calculate the main cluster mass at the given (cosmic) time.

        Parameters
        ----------
        t : float
            The (cosmic) time/age.
            Unit: [Gyr]

        Returns
        -------
        mass : float
            The mass of the main cluster.
            Unit: [Msun]
        """
        merger1 = self._merger(t)
        idx1 = merger1["idx"]
        mass1 = merger1["M_main"]
        t1 = merger1["age"]
        if idx1 == 0:
            mass0 = self.M_obs
            t0 = self.age_obs
        else:
            idx0 = idx1 - 1
            mass0 = self.M_main[idx0]
            t0 = self.age_merger[idx0]
        rate = (mass0 - mass1) / (t0 - t1)
        return (mass1 + rate * (t - t1))

    @property
    def time_turbulence_avg(self):
        """
        Calculate the time-averaged turbulence acceleration active time
        within the period from ``age_begin`` to ``age_obs``.

        Unit: [Gyr]
        """
        dt = self.time_step
        xt = np.arange(self.age_begin, self.age_obs+dt/2, step=dt)
        t_turb = np.array([self.time_turbulence(t) for t in xt])
        return np.sum(t_turb * dt) / (len(xt) * dt)

    @property
    def mach_turbulence_avg(self):
        """
        Calculate the time-averaged turbulence Mach number within the
        period from ``age_begin`` to ``age_obs``.
        """
        dt = self.time_step
        xt = np.arange(self.age_begin, self.age_obs+dt/2, step=dt)
        mach = np.array([self.mach_turbulence(t) for t in xt])
        return np.sum(mach * dt) / (len(xt) * dt)

    @property
    def tau_acceleration_avg(self):
        """
        Calculate the time-averaged turbulence acceleration timescale
        (i.e., efficiency) within the period from ``age_begin`` to
        ``age_obs``.

        Unit: [Gyr]
        """
        dt = self.time_step
        xt = np.arange(self.age_begin, self.age_obs+dt/2, step=dt)
        tau = np.array([self.tau_acceleration(t) for t in xt])
        return np.sum(tau * dt) / (len(xt) * dt)

    @property
    def time_acceleration_fraction(self):
        """
        Calculate the fraction of time within the period from
        ``age_begin`` to ``age_obs`` that the turbulence acceleration
        is active.
        """
        dt = self.fpsolver.tstep
        xt = np.arange(self.age_begin, self.age_obs+dt/2, step=dt)
        active = np.array([self._is_turb_active(t) for t in xt], dtype=int)
        return active.mean()
