import numpy as np
import torch
import warnings
from .neurodiffeq import safe_diff as diff


class BaseCondition:
    r"""Base class for all conditions.

    A condition is a tool to `re-parameterize` the output(s) of a neural network.
    such that the re-parameterized output(s) will automatically satisfy initial conditions (ICs)
    and boundary conditions (BCs) of the PDEs/ODEs that are being solved.

    .. note::
        - The nouns *(re-)parameterization* and *condition* are used interchangeably in the documentation and library.
        - The verbs *(re-)parameterize* and *enforce* are different in that:

          - *(re)parameterize* is said of network outputs;
          - *enforce* is said of networks themselves.
    """

    def __init__(self):
        self.ith_unit = None

    def parameterize(self, output_tensor, *input_tensors):
        r"""Re-parameterizes output(s) of a network.

        :param output_tensor: Output of the neural network.
        :type output_tensor: `torch.Tensor`
        :param input_tensors: Inputs to the neural network; i.e., sampled coordinates; i.e., independent variables.
        :type input_tensors: `torch.Tensor`
        :return: The re-parameterized output of the network.
        :rtype: `torch.Tensor`

        .. note:: 
            This method is **abstract** for BaseCondition
        """
        raise ValueError(f"Abstract {self.__class__.__name__} cannot be parameterized")

    def enforce(self, net, *coordinates):
        r"""Enforces this condition on a network.

        :param net: The network whose output is to be re-parameterized.
        :type net: `torch.nn.Module`
        :param coordinates: Inputs of the neural network.
        :type coordinates: `torch.Tensor`
        :return: The re-parameterized output, where the condition is automatically satisfied.
        :rtype: `torch.Tensor`
        """
        # concatenate the coordinates and pass to network
        network_output = net(torch.cat(coordinates, dim=1))
        # if `ith_unit` is set, the condition will only be enforced on the i-th output unit
        if self.ith_unit is not None:
            network_output = network_output[:, self.ith_unit].view(-1, 1)
        # parameterize the raw output and return
        return self.parameterize(network_output, *coordinates)

    def set_impose_on(self, ith_unit):
        r"""**[DEPRECATED]** When training several functions with a single, multi-output network, this method is called
        (by a `Solver` class or a `solve` function) to keep track of which output is being parameterized.

        :param ith_unit: The index of network output to be parameterized.
        :type ith_unit: int

        .. note::
            This method is deprecated and retained for backward compatibility only. Users interested in enforcing
            conditions on multi-output networks should consider using a ``neurodiffeq.neurodiffeq.EnsembleCondition``.
        """

        warnings.warn(f"`{self.__class__.__name__}.set_impose_on` is deprecated and will be removed in the future")
        self.ith_unit = ith_unit


class IrregularBoundaryCondition(BaseCondition):
    # Is there a more elegant solution?
    def in_domain(self, *coordinates):
        """Given the coordinates (numpy.ndarray), the methods returns an boolean array indicating
        whether the points lie within the domain.

        :param coordinates: Input tensors, each with shape (n_samples, 1).
        :type coordinates: `numpy.ndarray`
        :return: Whether each point lies within the domain.
        :rtype: `numpy.ndarray`

        .. note::
            - This method is meant to be used by monitors for irregular domain visualization.
        """

        # returns straight `True`-s by default; i.e., all points are considered within domain
        return np.ones_like(coordinates[0], dtype=np.bool)


class EnsembleCondition(BaseCondition):
    r"""An ensemble condition that enforces sub-conditions on individual output units of the networks.

    :param sub_conditions: Condition(s) to be ensemble'd.
    :type sub_conditions: BaseCondition
    :param force: Whether or not to force ensembl'ing even when `.enforce` is overridden in one of the sub-conditions.
    :type force: bool
    """

    def __init__(self, *sub_conditions, force=False):
        super(EnsembleCondition, self).__init__()
        for i, c in enumerate(sub_conditions):
            if c.__class__.enforce != BaseCondition.enforce:
                msg = f"{c.__class__.__name__} (index={i})'s overrides BaseCondition's `.enforce` method. " \
                      f"Ensembl'ing is likely not going to work."
                if force:
                    warnings.warn(msg)
                else:
                    raise ValueError(msg + "\nTry with `force=True` if you know what you are doing.")

        self.conditions = sub_conditions

    def parameterize(self, output_tensor, *input_tensors):
        r"""Re-parameterizes each column in output_tensor individually, using its corresponding sub-condition.
        This is useful when solving differential equations with a single, multi-output network.

        :param output_tensor: Output of the neural network. Number of units (.shape[1]) must equal number of sub-conditions.
        :type output_tensor: `torch.Tensor`
        :param input_tensors: Inputs to the neural network; i.e., sampled coordinates; i.e., independent variables.
        :type input_tensors: `torch.Tensor`
        :return: The column-wise re-parameterized network output, concatenated across columns so that it's still one tensor.
        :rtype: `torch.Tensor`
        """
        if output_tensor.shape[1] != len(self.conditions):
            raise ValueError(f"number of output units ({output_tensor.shape[1]}) "
                             f"differs from number of conditions ({len(self.conditions)})")
        return torch.cat([
            con.parameterize(output_tensor[:, i].view(-1, 1), *input_tensors) for i, con in enumerate(self.conditions)
        ], dim=1)


class NoCondition(BaseCondition):
    r"""A polymorphic condition where no re-parameterization will be performed.

    .. note::
        This condition is called *polymorphic* because it can be enforced on networks of arbitrary input/output sizes.
    """

    def parameterize(self, output_tensor, *input_tensors):
        f"""Performs no re-parameterization, or identity parameterization, in this case.

        :param output_tensor: Output of the neural network.
        :type output_tensor: `torch.Tensor`
        :param input_tensors: Inputs to the neural network; i.e., sampled coordinates; i.e., independent variables.
        :type input_tensors: `torch.Tensor`
        :return: The re-parameterized output of the network.
        :rtype: `torch.Tensor`
        """
        return output_tensor


class IVP(BaseCondition):
    r"""An initial value problem of one of the following forms:

    - Dirichlet condition: :math:`x(t)\bigg|_{t = t_0} = x_0`.
    - Neumann condition: :math:`\displaystyle\frac{\partial x}{\partial t}\bigg|_{t = t_0} = x_0'`.

    :param t_0: The initial time.
    :type t_0: float
    :param x_0: The initial value of :math:`x`. :math:`x(t)\bigg|_{t = t_0} = x_0`.
    :type x_0: float
    :param x_0_prime: The initial derivative of :math:`x` w.r.t. :math:`t`. :math:`\displaystyle\frac{\partial x}{\partial t}\bigg|_{t = t_0} = x_0'`, defaults to None.
    :type x_0_prime: float, optional
    """

    def __init__(self, t_0, x_0, x_0_prime=None):
        super().__init__()
        self.t_0, self.x_0, self.x_0_prime = t_0, x_0, x_0_prime

    def parameterize(self, output_tensor, t):
        r"""Re-parameterizes outputs such that the Dirichlet/Neumann condition is satisfied.

        - For Dirichlet condition, the re-parameterization is
          :math:`\displaystyle x(t) = x_0 + \left(1 - e^{-(t-t_0)}\right) \mathrm{ANN}(t)`
          where :math:`\mathrm{ANN}` is the neural network.
        - For Neumann condition, the re-parameterization is
          :math:`\displaystyle x(t) = x_0 + (t-t_0) x'_0 + \left(1 - e^{-(t-t_0)}\right)^2 \mathrm{ANN}(t)`
          where :math:`\mathrm{ANN}` is the neural network.

        :param output_tensor: Output of the neural network.
        :type output_tensor: `torch.Tensor`
        :param t: Input to the neural network; i.e., sampled time-points; i.e., independent variables.
        :type t: `torch.Tensor`
        :return: The re-parameterized output of the network.
        :rtype: `torch.Tensor`
        """
        if self.x_0_prime is None:
            return self.x_0 + (1 - torch.exp(-t + self.t_0)) * output_tensor
        else:
            return self.x_0 + (t - self.t_0) * self.x_0_prime + ((1 - torch.exp(-t + self.t_0)) ** 2) * output_tensor


class DirichletBVP(BaseCondition):
    r"""A double-ended Dirichlet boundary condition:
    :math:`x(t)\bigg|_{t = t_0} = x_0` and :math:`x(t)\bigg|_{t = t_1} = x_1`.

    :param t_0: The initial time.
    :type t_0: float
    :param t_1: The final time.
    :type t_1: float
    :param x_0: The initial value of :math:`x`. :math:`x(t)\bigg|_{t = t_0} = x_0`.
    :type x_0: float
    :param x_1: The initial value of :math:`x`. :math:`x(t)\bigg|_{t = t_1} = x_1`.
    :type x_1: float
    """

    def __init__(self, t_0, x_0, t_1, x_1):
        super().__init__()
        self.t_0, self.x_0, self.t_1, self.x_1 = t_0, x_0, t_1, x_1

    def parameterize(self, output_tensor, t):
        r"""Re-parameterizes outputs such that the Dirichlet condition is satisfied on both ends of the domain.

        The re-parameterization is
        :math:`\displaystyle x(t)=(1-\tilde{t})x_0+\tilde{t}x_1+\left(1-e^{(1-\tilde{t})\tilde{t}}\right)\mathrm{ANN}(t)`,
        where :math:`\displaystyle \tilde{t} = \frac{t-t_0}{t_1-t_0}` and :math:`\mathrm{ANN}` is the neural network.

        :param output_tensor: Output of the neural network.
        :type output_tensor: `torch.Tensor`
        :param t: Input to the neural network; i.e., sampled time-points or another independent variable.
        :type t: `torch.Tensor`
        :return: The re-parameterized output of the network.
        :rtype: `torch.Tensor`
        """

        t_tilde = (t - self.t_0) / (self.t_1 - self.t_0)
        return self.x_0 * (1 - t_tilde) \
               + self.x_1 * t_tilde \
               + (1 - torch.exp((1 - t_tilde) * t_tilde)) * output_tensor


class DirichletBVP2D(BaseCondition):
    r"""An Dirichlet boundary condition on the boundary of :math:`[x_0, x_1] \times [y_0, y_1]`, where

    - :math:`u(x, y)\bigg|_{x = x_0} = f_0(y)`;
    - :math:`u(x, y)\bigg|_{x = x_1} = f_1(y)`;
    - :math:`u(x, y)\bigg|_{y = y_0} = g_0(x)`;
    - :math:`u(x, y)\bigg|_{y = y_1} = g_1(x)`.

    :param x_min: The lower bound of x, the :math:`x_0`.
    :type x_min: float
    :param x_min_val: The boundary value on :math:`x = x_0`, i.e. :math:`f_0(y)`.
    :type x_min_val: callable
    :param x_max: The upper bound of x, the :math:`x_1`.
    :type x_max: float
    :param x_max_val: The boundary value on :math:`x = x_1`, i.e. :math:`f_1(y)`.
    :type x_max_val: callable
    :param y_min: The lower bound of y, the :math:`y_0`.
    :type y_min: float
    :param y_min_val: The boundary value on :math:`y = y_0`, i.e. :math:`g_0(x)`.
    :type y_min_val: callable
    :param y_max: The upper bound of y, the :math:`y_1`.
    :type y_max: float
    :param y_max_val: The boundary value on :math:`y = y_1`, i.e. :math:`g_1(x)`.
    :type y_max_val: callable
    """

    def __init__(self, x_min, x_min_val, x_max, x_max_val, y_min, y_min_val, y_max, y_max_val):
        r"""Initializer method
        """
        super().__init__()
        self.x0, self.f0 = x_min, x_min_val
        self.x1, self.f1 = x_max, x_max_val
        self.y0, self.g0 = y_min, y_min_val
        self.y1, self.g1 = y_max, y_max_val

    def parameterize(self, output_tensor, x, y):
        r"""Re-parameterizes outputs such that the Dirichlet condition is satisfied on all four sides of the domain.

        The re-parameterization is
        :math:`\displaystyle u(x,y)=A(x,y)
        +\tilde{x}\left(1-\tilde{x}\right)\tilde{y}\left(1-\tilde{y}\right)\mathrm{ANN}(x,y)`, where

        :math:`\displaystyle \begin{align*}
        A(x,y)=&\left(1-\tilde{x}\right)f_0(y)+\tilde{x}f_1(y) \\
        &+\left(1-\tilde{y}\right)\left(g_0(x)-\left(1-\tilde{x}\right)g_0(x_0)+\tilde{x}g_0(x_1)\right) \\
        &+\tilde{y}\left(g_1(x)-\left(1-\tilde{x}\right)g_1(x_0)+\tilde{x}g_1(x_1)\right)
        \end{align*}`

        :math:`\displaystyle\tilde{x}=\frac{x-x_0}{x_1-x_0}`,

        :math:`\displaystyle\tilde{y}=\frac{y-y_0}{y_1-y_0}`,

        and :math:`\mathrm{ANN}` is the neural network.

        :param output_tensor: Output of the neural network.
        :type output_tensor: `torch.Tensor`
        :param x: :math:`x`-coordinates of inputs to the neural network; i.e., the sampled :math:`x`-coordinates
        :type x: `torch.Tensor`
        :param y: :math:`y`-coordinates of inputs to the neural network; i.e., the sampled :math:`y`-coordinates
        :type y: `torch.Tensor`
        :return: The re-parameterized output of the network.
        :rtype: `torch.Tensor`
        """
        x_tilde = (x - self.x0) / (self.x1 - self.x0)
        y_tilde = (y - self.y0) / (self.y1 - self.y0)
        x0 = torch.ones_like(x_tilde[0, 0]).expand(*x_tilde.shape) * self.x0
        x1 = torch.ones_like(x_tilde[0, 0]).expand(*x_tilde.shape) * self.x1
        Axy = (1 - x_tilde) * self.f0(y) + x_tilde * self.f1(y) \
              + (1 - y_tilde) * (self.g0(x) - ((1 - x_tilde) * self.g0(x0) + x_tilde * self.g0(x1))) \
              + y_tilde * (self.g1(x) - ((1 - x_tilde) * self.g1(x0) + x_tilde * self.g1(x1)))

        return Axy + x_tilde * (1 - x_tilde) * y_tilde * (1 - y_tilde) * output_tensor


class IBVP1D(BaseCondition):
    r"""An initial & boundary condition on a 1-D range where :math:`x\in[x_0, x_1]` and time starts at :math:`t_0`.
    The conditions should have the following parts:

    - :math:`u(x,t_0)=u_0(x)`;
    - :math:`u(x_0,t)=g(t)` or :math:`u'_x(x_0,t)=p(t)`;
    - :math:`u(x_1,t)=h(t)` or :math:`u'_x(x_1,t)=q(t)`.

    :param x_min: The lower bound of x, the :math:`x_0`.
    :type x_min: float
    :param x_max: The upper bound of x, the :math:`x_1`.
    :type x_max: float
    :param t_min: The initial time, the :math:`t_0`.
    :type t_min: float
    :param t_min_val: The initial condition, the :math:`u_0(x)`.
    :type t_min_val: callable
    :param x_min_val: The Dirichlet boundary condition when :math:`x = x_0`, the :math:`u(x_0, t)`, defaults to None.
    :type x_min_val: callable, optional
    :param x_min_prime: The Neumann boundary condition when :math:`x = x_0`, the :math:`u'_x(x_0, t)`, defaults to None.
    :type x_min_prime: callable, optional
    :param x_max_val: The Dirichlet boundary condition when :math:`x = x_1`, the :math:`u(x_1, t)`, defaults to None.
    :type x_max_val: callable, optional
    :param x_max_prime: The Neumann boundary condition when :math:`x = x_1`, the :math:`u'_x(x_1, t)`, defaults to None.
    :type x_max_prime: callable, optional
    :raises NotImplementedError: When unimplemented boundary conditions are configured.

    .. note::
        This condition cannot be passed to ``neurodiffeq.conditions.EnsembleCondition`` unless both boundaries uses
        Dirichlet conditions (by specifying only ``x_min_val`` and ``x_max_val``) and ``force`` is set to True in
        EnsembleCondition's constructor.
    """

    def __init__(
            self, x_min, x_max, t_min, t_min_val,
            x_min_val=None, x_min_prime=None,
            x_max_val=None, x_max_prime=None,
    ):
        super().__init__()
        n_conditions = sum(c is not None for c in [x_min_val, x_min_prime, x_max_val, x_max_prime])
        if n_conditions != 2 or (x_min_val and x_min_prime) or (x_max_val and x_max_prime):
            raise NotImplementedError('Sorry, this boundary condition is not implemented.')
        self.x_min, self.x_min_val, self.x_min_prime = x_min, x_min_val, x_min_prime
        self.x_max, self.x_max_val, self.x_max_prime = x_max, x_max_val, x_max_prime
        self.t_min, self.t_min_val = t_min, t_min_val

    def enforce(self, net, x, t):
        r"""Enforces this condition on a network with inputs `x` and `t`

        :param net: The network whose output is to be re-parameterized.
        :type net: `torch.nn.Module`
        :param x: The :math:`x`-coordinates of the samples; i.e., the spatial coordinates.
        :type x: `torch.Tensor`
        :param t: The :math:`t`-coordinates of the samples; i.e., the temporal coordinates.
        :type t: `torch.Tensor`
        :return: The re-parameterized output, where the condition is automatically satisfied.
        :rtype: `torch.Tensor`

        .. note::
            This method overrides the default method of ``neurodiffeq.conditions.BaseCondition`` .
            In general, you should avoid overriding ``enforce`` when implementing custom boundary conditions.
        """

        def ANN(x, t):
            out = net(torch.cat([x, t], dim=1))
            if self.ith_unit is not None:
                out = out[:, self.ith_unit].view(-1, 1)
            return out

        uxt = ANN(x, t)
        if self.x_min_val and self.x_max_val:
            return self.parameterize(uxt, x, t)
        elif self.x_min_val and self.x_max_prime:
            x1 = self.x_max * torch.ones_like(x, requires_grad=True)
            ux1t = ANN(x1, t)
            return self.parameterize(uxt, x, t, ux1t, x1)
        elif self.x_min_prime and self.x_max_val:
            x0 = self.x_min * torch.ones_like(x, requires_grad=True)
            ux0t = ANN(x0, t)
            return self.parameterize(uxt, x, t, ux0t, x0)
        elif self.x_min_prime and self.x_max_prime:
            x0 = self.x_min * torch.ones_like(x, requires_grad=True)
            x1 = self.x_max * torch.ones_like(x, requires_grad=True)
            ux0t = ANN(x0, t)
            ux1t = ANN(x1, t)
            return self.parameterize(uxt, x, t, ux0t, x0, ux1t, x1)
        else:
            raise NotImplementedError('Sorry, this boundary condition is not implemented.')

    def parameterize(self, u, x, t, *additional_tensors):
        r"""Re-parameterizes outputs such that the initial and boundary conditions are satisfied.

        The Initial condition is always :math:`u(x,t_0)=u_0(x)`. There are four boundary conditions that are
        currently implemented:

        - For Dirichlet-Dirichlet boundary condition :math:`u(x_0,t)=g(t)` and :math:`u(x_1,t)=h(t)`:

          The re-parameterization is
          :math:`\displaystyle u(x,t)=A(x,t)+\tilde{x}\big(1-\tilde{x}\big)\Big(1-e^{-\tilde{t}}\Big)\mathrm{ANN}(x,t)`,
          where :math:`\displaystyle A(x,t)=u_0(x)+
          \tilde{x}\big(h(t)-h(t_0)\big)+\big(1-\tilde{x}\big)\big(g(t)-g(t_0)\big)`.

        - For Dirichlet-Neumann boundary condition :math:`u(x_0,t)=g(t)` and :math:`u'_x(x_1, t)=q(t)`:

          The re-parameterization is
          :math:`\displaystyle u(x,t)=A(x,t)+\tilde{x}\Big(1-e^{-\tilde{t}}\Big)
          \Big(\mathrm{ANN}(x,t)-\big(x_1-x_0\big)\mathrm{ANN}'_x(x_1,t)-\mathrm{ANN}(x_1,t)\Big)`,
          where :math:`\displaystyle A(x,t)=u_0(x)+\big(x-x_0\big)\big(q(t)-q(t_0)\big)+\big(g(t)-g(t_0)\big)`.

        - For Neumann-Dirichlet boundary condition :math:`u'_x(x_0,t)=p(t)` and :math:`u(x_1, t)=h(t)`:

          The re-parameterization is
          :math:`\displaystyle u(x,t)=A(x,t)+\big(1-\tilde{x}\big)\Big(1-e^{-\tilde{t}}\Big)
          \Big(\mathrm{ANN}(x,t)-\big(x_1-x_0\big)\mathrm{ANN}'_x(x_0,t)-\mathrm{ANN}(x_0,t)\Big)`,
          where :math:`\displaystyle A(x,t)=u_0(x)+\big(x_1-x\big)\big(p(t)-p(t_0)\big)+\big(h(t)-h(t_0)\big)`.

        - For Neumann-Neumann boundary condition :math:`u'_x(x_0,t)=p(t)` and :math:`u'_x(x_1, t)=q(t)`

          The re-parameterization is
          :math:`\displaystyle u(x,t)=A(x,t)+\left(1-e^{-\tilde{t}}\right)
          \Big(
          \mathrm{ANN}(x,t)-\big(x-x_0\big)\mathrm{ANN}'_x(x_0,t)
          +\frac{1}{2}\tilde{x}^2\big(x_1-x_0\big)
          \big(\mathrm{ANN}'_x(x_0,t)-\mathrm{ANN}'_x(x_1,t)\big)
          \Big)`,
          where :math:`\displaystyle A(x,t)=u_0(x)
          -\frac{1}{2}\big(1-\tilde{x}\big)^2\big(x_1-x_0\big)\big(p(t)-p(t_0)\big)
          +\frac{1}{2}\tilde{x}^2\big(x_1-x_0\big)\big(q(t)-q(t_0)\big)`.

        Notations:

        - :math:`\displaystyle\tilde{t}=\frac{t-t_0}{t_1-t_0}`,
        - :math:`\displaystyle\tilde{x}=\frac{x-x_0}{x_1-x_0}`,
        - :math:`\displaystyle\mathrm{ANN}` is the neural network,
        - and :math:`\displaystyle\mathrm{ANN}'_x=\frac{\partial ANN}{\partial x}`.

        :param output_tensor: Output of the neural network.
        :type output_tensor: `torch.Tensor`
        :param x: The :math:`x`-coordinates of the samples; i.e., the spatial coordinates.
        :type x: `torch.Tensor`
        :param t: The :math:`t`-coordinates of the samples; i.e., the temporal coordinates.
        :type t: `torch.Tensor`
        :param additional_tensors: additional tensors that will be passed by ``enforce``
        :type additional_tensors: `torch.Tensor`
        :return: The re-parameterized output of the network.
        :rtype: `torch.Tensor`
        """

        t0 = self.t_min * torch.ones_like(t, requires_grad=True)
        x_tilde = (x - self.x_min) / (self.x_max - self.x_min)
        t_tilde = t - self.t_min

        if self.x_min_val and self.x_max_val:
            return self._parameterize_dd(u, x, t, x_tilde, t_tilde, t0)
        elif self.x_min_val and self.x_max_prime:
            return self._parameterize_dn(u, x, t, x_tilde, t_tilde, t0, *additional_tensors)
        elif self.x_min_prime and self.x_max_val:
            return self._parameterize_nd(u, x, t, x_tilde, t_tilde, t0, *additional_tensors)
        elif self.x_min_prime and self.x_max_prime:
            return self._parameterize_nn(u, x, t, x_tilde, t_tilde, t0, *additional_tensors)
        else:
            raise NotImplementedError('Sorry, this boundary condition is not implemented.')

    # When we have Dirichlet boundary conditions on both ends of the domain:
    def _parameterize_dd(self, uxt, x, t, x_tilde, t_tilde, t0):
        Axt = self.t_min_val(x) + \
              x_tilde * (self.x_max_val(t) - self.x_max_val(t0)) + \
              (1 - x_tilde) * (self.x_min_val(t) - self.x_min_val(t0))
        return Axt + x_tilde * (1 - x_tilde) * (1 - torch.exp(-t_tilde)) * uxt

    # When we have Dirichlet boundary condition on the left end of the domain
    # and Neumann boundary condition on the right end of the domain:
    def _parameterize_dn(self, uxt, x, t, x_tilde, t_tilde, t0, ux1t, x1):
        Axt = (self.x_min_val(t) - self.x_min_val(t0)) + self.t_min_val(x) + \
              x_tilde * (self.x_max - self.x_min) * (self.x_max_prime(t) - self.x_max_prime(t0))
        return Axt + x_tilde * (1 - torch.exp(-t_tilde)) * (
                uxt - (self.x_max - self.x_min) * diff(ux1t, x1) - ux1t
        )

    # When we have Neumann boundary condition on the left end of the domain
    # and Dirichlet boundary condition on the right end of the domain:
    def _parameterize_nd(self, uxt, x, t, x_tilde, t_tilde, t0, ux0t, x0):
        Axt = (self.x_max_val(t) - self.x_max_val(t0)) + self.t_min_val(x) + \
              (x_tilde - 1) * (self.x_max - self.x_min) * (self.x_min_prime(t) - self.x_min_prime(t0))
        return Axt + (1 - x_tilde) * (1 - torch.exp(-t_tilde)) * (
                uxt + (self.x_max - self.x_min) * diff(ux0t, x0) - ux0t
        )

    # When we have Neumann boundary conditions on both ends of the domain:
    def _parameterize_nn(self, uxt, x, t, x_tilde, t_tilde, t0, ux0t, x0, ux1t, x1):
        Axt = self.t_min_val(x) \
              - 0.5 * (1 - x_tilde) ** 2 * (self.x_max - self.x_min) * (self.x_min_prime(t) - self.x_min_prime(t0)) \
              + 0.5 * x_tilde ** 2 * (self.x_max - self.x_min) * (self.x_max_prime(t) - self.x_max_prime(t0))
        return Axt + (1 - torch.exp(-t_tilde)) * (
                uxt
                - x_tilde * (self.x_max - self.x_min) * diff(ux0t, x0)
                + 0.5 * x_tilde ** 2 * (self.x_max - self.x_min) * (
                        diff(ux0t, x0) - diff(ux1t, x1)
                )
        )


# TODO: reduce duplication
class DirichletBVPSpherical(BaseCondition):
    r"""The Dirichlet boundary condition for the interior and exterior boundary of the sphere,
    where the interior boundary is not necessarily a point. The conditions are:

    - :math:`u(r_0,\theta,\phi)=f(\theta,\phi)`
    - :math:`u(r_1,\theta,\phi)=g(\theta,\phi)`

    :param r_0: The radius of the interior boundary. When :math:`r_0 = 0`, the interior boundary collapses to a single point (center of the ball).
    :type r_0: float
    :param f: The value of :math:`u` on the interior boundary. :math:`u(r_0, \theta, \phi)=f(\theta, \phi)`.
    :type f: callable
    :param r_1: The radius of the exterior boundary; if set to None, `g` must also be None.
    :type r_1: float or None
    :param g: The value of :math:`u` on the exterior boundary. :math:`u(r_1, \theta, \phi)=g(\theta, \phi)`. If set to None, `r_1` must also be set to None.
    :type g: callable or None
    """

    def __init__(self, r_0, f, r_1=None, g=None):
        super(DirichletBVPSpherical, self).__init__()
        if (r_1 is None) ^ (g is None):
            raise ValueError(f'r_1 and g must be both/neither set to None; got r_1={r_1}, g={g}')
        self.r_0, self.r_1 = r_0, r_1
        self.f, self.g = f, g

    def parameterize(self, output_tensor, r, theta, phi):
        r"""Re-parameterizes outputs such that the Dirichlet condition is satisfied on both ends of the domain.

        - If both inner and outer boundaries are specified
          :math:`u(r_0,\theta,\phi)=f(\theta,\phi)` and
          :math:`u(r_1,\theta,\phi)=g(\theta,\phi)`:

          The re-parameterization is
          :math:`\big(1-\tilde{r}\big)f(\theta,\phi)+\tilde{r}g(\theta,\phi)
          +\Big(1-e^{\tilde{r}(1-{\tilde{r}})}\Big)\mathrm{ANN(r, \theta, \phi)}`

        - If only one boundary is specified (inner or outer) :math:`u(r_0,\theta,\phi)=f(\theta,\phi)`

          The re-parameterization is
          :math:`f(\theta,\phi)+\Big(1-e^{-|r-r_0|}\Big)\mathrm{ANN(r, \theta, \phi)}`

        :param output_tensor: Output of the neural network.
        :type output_tensor: `torch.Tensor`
        :param r: The radii (or :math:`r`-component) of the inputs to the network.
        :type r: `torch.Tensor`
        :param theta: The co-latitudes (or :math:`\theta`-component) of the inputs to the network.
        :type theta: `torch.Tensor`
        :param phi: The longitudes (or :math:`\phi`-component) of the inputs to the network.
        :type phi: `torch.Tensor`
        :return: The re-parameterized output of the network.
        :rtype: `torch.Tensor`
        """
        if self.r_1 is None:
            return (1 - torch.exp(-torch.abs(r - self.r_0))) * output_tensor + self.f(theta, phi)
        else:
            r_tilde = (r - self.r_0) / (self.r_1 - self.r_0)
            return self.f(theta, phi) * (1 - r_tilde) + \
                   self.g(theta, phi) * r_tilde + \
                   (1. - torch.exp((1 - r_tilde) * r_tilde)) * output_tensor
