# Copyright (c) 2016 Weitian LI <liweitianux@live.com>
# MIT license

"""
Handle the AJAX requests from the client to manage the simulation products.
"""


import os
import logging
import shutil

import tornado.ioloop
from tornado.escape import json_decode, json_encode

from .base import BaseRequestHandler


logger = logging.getLogger(__name__)


class ProductsAJAXHandler(BaseRequestHandler):
    """
    Handle the AJAX requests from the client to manage the simulation products.

    Attributes
    ----------
    from_localhost : bool
        ``True`` if the request is from the localhost, otherwise ``False``.
    """
    def initialize(self):
        """Hook for subclass initialization.  Called for each request."""
        self.products = self.application.products
        if self.request.remote_ip == "127.0.0.1":
            self.from_localhost = True
        else:
            self.from_localhost = False

    def get(self):
        """
        Handle the READ-ONLY products manifest manipulations.

        Supported actions:
        - get: Get the current products manifest
        - which: Locate the command/program (check whether the command/program
                 can be found in PATH and is executable)
        - download: TODO
        - open: TODO
        """
        action = self.get_argument("action", "get")
        if action == "get":
            # Get current products manifest
            response = {
                "manifest": self.products.manifest,
                "localhost": self.from_localhost,
            }
            success = True
        elif action == "which":
            # Locate (and check) the command/program
            cmd = json_decode(self.get_argument("cmd"))
            cmdpath = shutil.which(cmd)
            if cmdpath:
                success = True
                response = {
                    "isExecutable": True,
                    "cmdPath": cmdpath,
                }
            else:
                success = False
                reason = "Cannot locate the executable for: {0}".format(cmd)
        else:
            # ERROR: bad action
            success = False
            reason = "Bad request action: {0}".format(action)
        #
        if success:
            logger.debug("Response: {0}".format(response))
            self.set_header("Content-Type", "application/json; charset=UTF-8")
            self.write(json_encode(response))
        else:
            logger.warning("Request failed: {0}".format(reason))
            self.send_error(400, reason=reason)

    @tornado.web.authenticated
    def post(self):
        """
        Handle the READ-WRITE products manifest manipulations.

        Supported actions:
        - load: Load the products manifest from file
        - save: Save the current products manifest to file
        - reset: Reset existing products manifest
        - convert: Convert the product from HEALPix map to HPX image
        """
        request = json_decode(self.request.body)
        logger.debug("Received request: {0}".format(request))
        action = request.get("action")
        response = {"action": action}
        if action == "load":
            # Load the manifest from supplied file
            try:
                success, reason = self._load_products(request["manifestfile"])
            except KeyError:
                success = False
                reason = "'manifestfile' is missing"
        elif action == "save":
            # Save current products manifest to file
            try:
                success, reason = self._save_products(request["outfile"],
                                                      request["clobber"])
            except KeyError:
                success = False
                reason = "'outfile' or 'clobber' is missing"
        elif action == "reset":
            # Reset existing products manifest
            success = self._reset_products()
        elif action == "convert":
            # Convert the product from HEALPix map to HPX image
            try:
                comp_id = request["compID"]
                freq_id = request["freqID"]
                success, reason = self._convert_hpx(comp_id, freq_id)
                data = self.products.get_product(comp_id, freq_id)
                response["data"] = data
            except KeyError:
                success = False
                reason = "'compID' or 'freqID' is missing"
        else:
            # ERROR: bad action
            success = False
            reason = "Bad request action: {0}".format(action)
        #
        if success:
            response["success"] = success
            logger.debug("Response: {0}".format(response))
            self.set_header("Content-Type", "application/json; charset=UTF-8")
            self.write(json_encode(response))
        else:
            logger.warning("Request failed: {0}".format(reason))
            self.send_error(400, reason=reason)

    def _reset_products(self):
        """Reset the existing products manifest."""
        self.products.reset()
        return True

    def _load_products(self, manifestfile):
        """
        Load the products manifest from file.

        Parameters
        ----------
        manifestfile: str
            The path to the products manifest file, which must be
            an *absolute path*.

        Returns
        -------
        success : bool
            ``True`` if the operation succeeded, otherwise, ``False``.
        error : str
            If failed, this ``error`` saves the details, otherwise, ``None``.
        """
        success = False
        error = None
        if os.path.isabs(os.path.expanduser(manifestfile)):
            self.products.load(manifestfile)
            success = True
        else:
            error = "Not an absolute path"
        return (success, error)

    def _save_products(self, outfile, clobber=False):
        """
        Save current products manifest to file.

        Parameters
        ----------
        outfile: str
            The filepath to the output manifest file, which must be
            an *absolute path*.
        clobber : bool, optional
            Whether overwrite the output file if already exists?

        Returns
        -------
        success : bool
            ``True`` if the operation succeeded, otherwise, ``False``.
        error : str
            If failed, this ``error`` saves the details, otherwise, ``None``.
        """
        success = False
        error = None
        try:
            self.products.dump(outfile, clobber=clobber)
            success = True
        except (ValueError, OSError) as e:
            error = str(e)
        return (success, error)

    def _convert_hpx(self, comp_id, freq_id):
        """
        Convert the HEALPix map of the product to HPX projected FITS image.

        FIXME/TODO: make this non-blocking!
        """
        success = False
        error = None
        try:
            self.products.convert_hpx(comp_id, freq_id, clobber=True)
            success = True
        except IOError as e:
            error = str(e)
        return (success, error)
