"""Taiwanese junior-high and senior-high questions from publishers' question banks (tc-edu): nine subjects of single-answer questions in the mock exams' format, one task each, read from a private dataset."""

from flock.eval.tasks.base import RowFields
from flock.eval.tasks.suite import Suite
from flock.eval.tasks.tw_mock_exams import Subject, exam_task

# Each subject's `shots` are its 5 shortest self-contained `dev` items (039.D6); `dev` is the
# lowest 1 percent of the subject's question groups by hash (039.D3).
SUBJECTS = (
    Subject(
        "hs_chinese",
        "國文",
        (
            "00ad3cddc231e8deaead02149ed08a28722ddc06925e90a7b63b52113f34e062",
            "003fbe85a904e89dee8aba3f615ced301a1a0ec8dc76f9bcd727697e9712825b",
            "00507db9dbdd92f4ecc0659e7a1988d9d51b38fc365a19a4a66a7386620baf80",
            "02bde4b50fbba4f9d6904715f47164b37924c8bdf3a9f815ac8af474a51c42b0",
            "01a82684623a163aecb3d02e811fec3edd481c99d650fd4efd766a6b0dbfca1e",
        ),
    ),
    Subject(
        "jh_chinese",
        "國文",
        (
            "00b37e6316ce6ef12f26db3be513904cedc069b5bd511ae7249b66ed1bbd06ac",
            "021dbe1c92b17d101a7bb4fb2883543a57a1542a9ada093d666d3b046a21f6c0",
            "0084f930b6e014c6115c5ffa041fed10fb0463b88653024b986522c37a4ce0c4",
            "00eaea4bbf907fb1fb252206d71639a8b2871e6fb8162fca7833e34759d3029b",
            "00932e94490e1904ab8163114e641870902c6f5098ab083bd46b7ac36db2c9d4",
        ),
    ),
    Subject(
        "jh_english",
        "英文",
        (
            "01c07987b49a5486513b5834dff777c5dc20308badd5501bde473d300dc6a2b2",
            "01a5461dbbabfb3b70c17c3a6a8a2fea01ed05826d6523e480dee6ae81fd7981",
            "001b05d2891efbd70321c9e274379e9d3c873178b8dbbc596abc98eabf8ae47e",
            "01ceff6209c3a6a84e4c82b1d6e1f957c18ef7bd4e2199f4d242251f74f70175",
            "021cfd18899d6cee749fe152f47c27f89c755fe2d7ee8e60ff6a200ecb41018a",
        ),
    ),
    Subject(
        "jh_math",
        "數學",
        (
            "010b169d0eb73dca417ddc934ac38f59c9bb6081dd3e37f722287f92bd92f904",
            "011cf241e1ddb9e09e7312ffc7eb5ad1cbed2aa8dffa22a8cc7d4137670f48b9",
            "004ae20f5e3fb21498a521dbc710bea3bf85955ccd47ce8d39a3bd3d9b0545f2",
            "013649f5c61c56ff13b887a6f947336c8935801f127a1eb4d44f6a725051278c",
            "009eac89181ea7868ed993ded9dff0a21f908d7b4c51ff4dd3040f02151a2155",
        ),
    ),
    Subject(
        "jh_science",
        "自然",
        (
            "01c44d33b7299f517c3c99171a63ca44591033d597468e1d603e0ef97b178b60",
            "01ee0665a227a098636f95e114ae7d1a1d874cc2fc0fc2ca74c8cbda977100ea",
            "00b28b7738690a9d610301e7d6f3093e0a242bdcc0bcca6bd06ae07fc13bd80d",
            "00f385e0d7bbda6c3892c0b48e863064da5b26fbacfbd28c693f393c38308475",
            "01ee49b7d51bb8872ac72b5f4bf2e1f12ee91285e81ae95d5be2e8f24dc00f26",
        ),
    ),
    Subject(
        "jh_social",
        "社會",
        (
            "0051fff654808e1401e549ebd456691325b9c2dcaf4279372ac6788b2c327d9d",
            "02055a9008dd5cbc1323e8198d9c67425ae4bf33a4ada9fb7ad7a14f59882bdf",
            "02608a438eee13e941eb59721bad868e9dcac593d240d0e2c68bffa03e1e39b1",
            "00c050d03933dbebeaecffb52b6af6a4ca56fa44763c22bdf713056b4a03745c",
            "01eeff6092984fd8610528fdc1fb5c211bc9b395c20ce6e3ef0c7690fc29e2c2",
        ),
    ),
    Subject(
        "jh_arts",
        "藝術",
        (
            "00e7e8cd23659b9526bfdf3d73472b44006a5938aa4bec3866e78975d0f02a63",
            "024b2751eac1df95d1fe4180dd466d761bbd37372756ec0a06d961db51120c4f",
            "006e152eacef8d092af9f197e40b60c8890f0252319fd9a2c0d9d24f369ae79f",
            "009107bd20a6a55e221d51c0c5e071b03ff4ab1a773796188738d2dd2b7e91b1",
            "012f9f20edc2ffe4c748ae11d33dbbfdc125fc2c83863f8414f3344b3a63df6d",
        ),
    ),
    Subject(
        "jh_health_and_pe",
        "健康與體育",
        (
            "006fae02751038c81ed501dd9e1014ad1b5cc5ed9e19ddbfa61a717ab4f1a682",
            "022c291af384863b04ce77ec7a6bf704b572de81ae5c815c6b89a83cfff4ea55",
            "013630ad78610b459c4de0d4927084b5f1b321bb7762d2634850ef92ac9e282a",
            "0188978358f14eb32ec67dc3b156b318fde15d8fb21973a0d78f432556758110",
            "0223c0db438a9beddc3042dcb36713c68be705afa388f07ae62b8ceb39b3f2f4",
        ),
    ),
    Subject(
        "jh_integrative_activities",
        "綜合活動",
        (
            "0225178a574ece06234f9cb3bf6c2cc34bcf6068c1000fe334fc574a538f0a8c",
            "019772c866e01449ea131ccca7a9344269e10915034483d9463e3698597258d0",
            "0184526e6e7f32e5be413e95af5347c4ba72f8a142f58925f6a6508c2baa2826",
            "00939ce98e09ccafa4db0f7cf8da43581d7a6a1fe5907b862d858604c434d470",
            "000ebcd023e411466e4bb980c77ff000fd97d2b2e0b27b5e31e1820c3af1d8fd",
        ),
    ),
)

# Private: only members of the Cerulean organization can run these tasks (039.D1). Every `test`
# row was read and passed before the set was frozen, so no row is filtered here.
TC_EDU_TASKS = tuple(
    exam_task(
        subject,
        prefix="tc_edu",
        repo="cerulean-works/tc-edu-exams",
        revision="66c8db07fa379e9ee88af646debd3d22aed6a49e",
        private=True,
        # The question bank's own id (`uid`; the doc's id is the row's `hash`), its record and
        # number, then where it comes from and how it was extracted and checked.
        row_fields=RowFields(
            native_id="uid",
            meta=(
                "record_id",
                "number",
                "publisher",
                "school_year_semester",
                "source_dataset",
                "extraction_method",
                "rights",
                "check",
            ),
        ),
    )
    for subject in SUBJECTS
)

# The subjects' unweighted mean, as the mock exams' (009.D5), and the junior-high subjects' alone.
JUNIOR_HIGH_TASKS = tuple(task for task in TC_EDU_TASKS if task.name.startswith("tc_edu_jh_"))
TC_EDU = Suite("tc_edu", TC_EDU_TASKS)
TC_EDU_JH = Suite("tc_edu:jh", JUNIOR_HIGH_TASKS)
TC_EDU_BPB = Suite("tc_edu:bpb", TC_EDU_TASKS, metric="bpb")
TC_EDU_JH_BPB = Suite("tc_edu:jh:bpb", JUNIOR_HIGH_TASKS, metric="bpb")
# An accuracy and a `bpb` don't average, so the group that runs both has no score.
TC_EDU_ALL = Suite("tc_edu:all", (TC_EDU, TC_EDU_JH, TC_EDU_BPB, TC_EDU_JH_BPB), scored=False)
