import base64
import errno
import io
import json
import logging
import os
import shutil
import subprocess
import uuid

from django.conf import settings
from django.utils.translation import gettext

HAS_SELENIUM = False
if settings.USE_SELENIUM:
    from selenium import webdriver
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    HAS_SELENIUM = True


HAS_PDF = (os.path.isdir(settings.DMOJ_PDF_PROBLEM_CACHE) and HAS_SELENIUM)

EXIFTOOL = settings.EXIFTOOL
HAS_EXIFTOOL = os.access(EXIFTOOL, os.X_OK)

logger = logging.getLogger('judge.problem.pdf')


class BasePdfMaker(object):
    math_engine = 'jax'
    title = None

    def __init__(self, dir=None, clean_up=True):
        self.dir = dir or os.path.join(settings.DMOJ_PDF_PROBLEM_TEMP_DIR, str(uuid.uuid1()))
        self.proc = None
        self.log = None
        self.htmlfile = os.path.join(self.dir, 'input.html')
        self.pdffile = os.path.join(self.dir, 'output.pdf')
        self.clean_up = clean_up

    def load(self, file, source):
        with open(os.path.join(self.dir, file), 'w') as target, open(source) as source:
            target.write(source.read())

    def make(self, debug=False):
        self._make(debug)

        if self.title and HAS_EXIFTOOL:
            try:
                subprocess.check_output([EXIFTOOL, '-Title=%s' % (self.title,), self.pdffile])
            except subprocess.CalledProcessError as e:
                logger.error('Failed to run exiftool to set title for: %s\n%s', self.title, e.output)

    def _make(self, debug):
        raise NotImplementedError()

    @property
    def html(self):
        with io.open(self.htmlfile, encoding='utf-8') as f:
            return f.read()

    @html.setter
    def html(self, data):
        with io.open(self.htmlfile, 'w', encoding='utf-8') as f:
            f.write(data)

    @property
    def success(self):
        return self.proc.returncode == 0

    @property
    def created(self):
        return os.path.exists(self.pdffile)

    def __enter__(self):
        try:
            os.makedirs(self.dir)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.clean_up:
            shutil.rmtree(self.dir, ignore_errors=True)


class SeleniumPDFRender(BasePdfMaker):
    success = False
    template = {
        'printBackground': True,
        'displayHeaderFooter': True,
        'headerTemplate': '<div></div>',
        'footerTemplate': '<center style="margin: 0 auto; font-family: Segoe UI; font-size: 10px">' +
                          gettext('Page %s of %s') %
                          ('<span class="pageNumber"></span>', '<span class="totalPages"></span>') +
                          '</center>',
    }

    def get_log(self, driver):
        return '\n'.join(map(str, driver.get_log('driver') + driver.get_log('browser')))

    def _make(self, debug):
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        #options.add_argument("--no-sandbox")
        options.binary_location = settings.SELENIUM_CUSTOM_CHROME_PATH

        browser = webdriver.Chrome(settings.SELENIUM_CHROMEDRIVER_PATH, options=options)
        browser.get('file://' + os.path.abspath(os.path.join(self.dir, 'input.html')))
        self.log = self.get_log(browser)

        try:
            WebDriverWait(browser, 15).until(EC.presence_of_element_located((By.CLASS_NAME, 'math-loaded')))
        except TimeoutException:
            logger.error('PDF math rendering timed out')
            self.log = self.get_log(browser) + '\nPDF math rendering timed out'
            return

        response = browser.execute_cdp_cmd('Page.printToPDF', self.template)
        self.log = self.get_log(browser)
        if not response:
            return

        with open(os.path.abspath(os.path.join(self.dir, 'output.pdf')), 'wb') as f:
            f.write(base64.b64decode(response['data']))

        self.success = True


if HAS_SELENIUM:
    DefaultPdfMaker = SeleniumPDFRender
else:
    DefaultPdfMaker = None
