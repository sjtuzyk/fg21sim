# Copyright (c) 2016-2017 Weitian LI <weitian@aaronly.me>
# MIT license
#
# Makefile for "fg21sim"
#
# Credit: http://blog.bottlepy.org/2012/07/16/virtualenv-and-makefiles.html
#

# Path to the virtualenv
VENV ?= venv


default:
	@echo "+-------------------------------------------------------------+"
	@echo "|                 Make Utility for fg21sim                    |"
	@echo "+-------------------------------------------------------------+"
	@echo "Available targets:"
	@echo "  + venv [VENV=<venv>]"
	@echo "        create virtualenv '${VENV}' and install the dependencies"
	@echo "  + devbuild"
	@echo "        (build and) install the package to the virtualenv"
	@echo "  + test"
	@echo "        run the tests"
	@echo "  + print-<VARIABLE>"
	@echo "        print the value of variable <VARIABLE>"

# Create virtualenv and install/update the dependencies
venv: ${VENV}/bin/activate
${VENV}/bin/activate: requirements.txt
	test -d ${VENV} || python3 -m venv ${VENV}
	${VENV}/bin/pip3 install -r requirements.txt
	touch ${VENV}/bin/activate

# Install this package to the virtualenv
devbuild: venv
	${VENV}/bin/python3 setup.py install

# Run the tests
test: devbuild
	${VENV}/bin/python3 setup.py test


# One liner to get the value of any makefile variable
# Credit: http://blog.jgc.org/2015/04/the-one-line-you-should-add-to-every.html
print-%: ; @echo $*=$($*)
