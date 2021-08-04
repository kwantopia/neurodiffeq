from abc import ABC, abstractmethod
import torch


class NumericalSolver(ABC):
    @abstractmethod
    def solve(self, func, u0, t0, tn, n_steps):
        pass


class Euler(NumericalSolver):
    def solve(self, func, u0, t0, tn, n_steps):
        ts = torch.linspace(t0, tn, n_steps + 1)
        if isinstance(u0, (float, int)):
            u0 = (u0,)
        if isinstance(u0, (list, tuple)):
            u0 = torch.tensor(u0)
        us = [u0]
        h = (tn - t0) / n_steps
        for t in ts[:-1]:
            u_old = us[-1]
            u_new = u_old + h * torch.tensor(func(*u_old, t))
            us.append(u_new)

        us = torch.stack(us, dim=0)
        ans = [ts]
        for j in range(us.shape[1]):
            ans.append(us[:, j])

        return ans
