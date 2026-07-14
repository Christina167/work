#include "mb.h"
#include "mbport.h"

/*
 * FreeModbus 的寄存器回调地址是 1 基地址。
 *
 * Modbus 协议请求地址 0
 * 对应回调中的 usAddress = 1。
 */
#define REG_HOLDING_START   1U
#define REG_HOLDING_NREGS   16U

static USHORT usHoldingBuf[REG_HOLDING_NREGS] =
{
    100U, 200U, 300U, 400U,
    500U, 600U, 700U, 800U,
    0U,   0U,   0U,   0U,
    0U,   0U,   0U,   0U
};

eMBErrorCode eMBRegHoldingCB(UCHAR *pucRegBuffer,
                             USHORT usAddress,
                             USHORT usNRegs,
                             eMBRegisterMode eMode)
{
    USHORT usIndex;

    if (usAddress < REG_HOLDING_START)
    {
        return MB_ENOREG;
    }

    usIndex = (USHORT)(usAddress - REG_HOLDING_START);

    if (((uint32_t)usIndex + usNRegs) > REG_HOLDING_NREGS)
    {
        return MB_ENOREG;
    }

    while (usNRegs > 0U)
    {
        if (eMode == MB_REG_READ)
        {
            /*
             * Modbus 寄存器在线路上传输时，高字节在前。
             */
            *pucRegBuffer++ =
                (UCHAR)(usHoldingBuf[usIndex] >> 8);

            *pucRegBuffer++ =
                (UCHAR)(usHoldingBuf[usIndex] & 0xFFU);
        }
        else
        {
            usHoldingBuf[usIndex] =
                (USHORT)((USHORT)(*pucRegBuffer++) << 8);

            usHoldingBuf[usIndex] |=
                (USHORT)(*pucRegBuffer++);
        }

        usIndex++;
        usNRegs--;
    }

    return MB_ENOERR;
}

eMBErrorCode eMBRegInputCB(UCHAR *pucRegBuffer,
                           USHORT usAddress,
                           USHORT usNRegs)
{
    (void)pucRegBuffer;
    (void)usAddress;
    (void)usNRegs;

    return MB_ENOREG;
}

eMBErrorCode eMBRegCoilsCB(UCHAR *pucRegBuffer,
                           USHORT usAddress,
                           USHORT usNCoils,
                           eMBRegisterMode eMode)
{
    (void)pucRegBuffer;
    (void)usAddress;
    (void)usNCoils;
    (void)eMode;

    return MB_ENOREG;
}

eMBErrorCode eMBRegDiscreteCB(UCHAR *pucRegBuffer,
                              USHORT usAddress,
                              USHORT usNDiscrete)
{
    (void)pucRegBuffer;
    (void)usAddress;
    (void)usNDiscrete;

    return MB_ENOREG;
}
