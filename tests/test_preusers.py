from io import BytesIO
import pytest
from openpyxl import Workbook
from fastapi import HTTPException
from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import Session
from app.world_music.preusers import parse_excel,import_excel
from app.world_music.models import Base
from app.world_music.preuser_models import Preuser,PreuserImport

def excel(rows):
    book=Workbook(write_only=True); sheet=book.create_sheet()
    for row in rows: sheet.append(row)
    stream=BytesIO();book.save(stream);return stream.getvalue()

def test_validation_and_no_implicit_subscription():
    data=excel([['email'],[' FAN@example.com '],['fan@example.com'],['bad'],['=1+1'],['otro@example.com']])
    emails,report=parse_excel(data)
    assert emails==['fan@example.com','otro@example.com']
    assert report['duplicates_in_file']==1 and report['invalid']==2
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert import_excel(data,'fans.xlsx',True,session)['new']==2
        assert session.scalar(select(func.count()).select_from(Preuser))==0
        assert import_excel(data,'fans.xlsx',False,session)['imported']==2
        assert import_excel(data,'fans.xlsx',False,session)['imported']==0
        assert all(u.status=='pre_registered' for u in session.scalars(select(Preuser)))

def test_50000_and_repeat():
    data=excel([['email']]+[[f'fan{i}@example.com'] for i in range(50000)])
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with Session(engine) as session:
        result=import_excel(data,'50000.xlsx',False,session)
        assert result['imported']==50000
        assert session.scalar(select(func.count()).select_from(Preuser))==50000
        assert session.scalar(select(PreuserImport)).imported==50000

@pytest.mark.parametrize('rows',[[['email','name'],['fan@example.com','Name']],[['email']]+[[f'a{i}@example.com'] for i in range(50001)]])
def test_reject_extra_data(rows):
    with pytest.raises(HTTPException):parse_excel(excel(rows))
