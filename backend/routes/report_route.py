from flask import Blueprint
from flask import send_file

from reportlab.platypus import SimpleDocTemplate
from reportlab.platypus import Paragraph
from reportlab.lib.styles import getSampleStyleSheet

import os


report_bp = Blueprint(
    'report',
    __name__
)


@report_bp.route('/report')
def report():

    filepath = 'report.pdf'

    doc = SimpleDocTemplate(
        filepath
    )

    styles = getSampleStyleSheet()

    elements = []

    elements.append(

        Paragraph(
            "BPOM Monitoring Report",
            styles['Title']
        )

    )

    doc.build(elements)

    return send_file(
        filepath,
        as_attachment=True
    )